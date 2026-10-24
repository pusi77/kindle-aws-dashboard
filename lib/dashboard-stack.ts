import { execFileSync } from 'node:child_process';
import { cpSync } from 'node:fs';
import { join } from 'node:path';
import {
  Annotations, Aws, type CfnElement, CfnOutput, Duration, RemovalPolicy, Stack, TimeZone, Validations, type StackProps,
} from 'aws-cdk-lib';
import { Alarm, ComparisonOperator, Metric, TreatMissingData } from 'aws-cdk-lib/aws-cloudwatch';
import { SnsAction } from 'aws-cdk-lib/aws-cloudwatch-actions';
import { PolicyStatement, Role, ServicePrincipal } from 'aws-cdk-lib/aws-iam';
import { Architecture, Code, Function, FunctionUrlAuthType, Runtime, type FunctionProps } from 'aws-cdk-lib/aws-lambda';
import { LogGroup, RetentionDays } from 'aws-cdk-lib/aws-logs';
import { BlockPublicAccess, Bucket, BucketEncryption } from 'aws-cdk-lib/aws-s3';
import { Schedule, ScheduleExpression } from 'aws-cdk-lib/aws-scheduler';
import { LambdaInvoke } from 'aws-cdk-lib/aws-scheduler-targets';
import { Topic } from 'aws-cdk-lib/aws-sns';
import { EmailSubscription } from 'aws-cdk-lib/aws-sns-subscriptions';
import type { Construct } from 'constructs';

const LAMBDAS = join(import.meta.dirname, '..', 'lambdas');
// Renders at these hours (Europe/Rome); the Kindle fetches at :05 (see NOTES.md for its env.sh).
const RENDER_HOURS = '7,11,15,19';

export interface DashboardStackProps extends StackProps {
  /** SSM SecureString holding the Kindle's bearer token, created outside CloudFormation. */
  readonly tokenParameterName: string;
  /** Where the low-battery alarm is sent. No subscription if unset. */
  readonly alarmEmail?: string;
}

export class DashboardStack extends Stack {
  constructor(scope: Construct, id: string, props: DashboardStackProps) {
    super(scope, id, props);

    // Holds only latest.png/latest.json, regenerated every render. Retained on stack deletion so
    // a destroy never fails on a non-empty bucket; delete it by hand if you tear the stack down.
    const bucket = new Bucket(this, 'Images', {
      blockPublicAccess: BlockPublicAccess.BLOCK_ALL,
      encryption: BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      removalPolicy: RemovalPolicy.RETAIN,
    });
    Validations.of(bucket).acknowledge({
      id: 'AwsSolutions::AwsSolutions-S1',
      reason: 'Private bucket with two regenerated objects, read and written by two Lambda roles only; access logs would add a bucket and cost for no audit value.',
    });

    const renderer = this.pythonFunction('Renderer', {
      code: Code.fromAsset(join(LAMBDAS, 'renderer'), { exclude: ['__pycache__'], bundling: rendererBundling() }),
      memorySize: 512,
      timeout: Duration.seconds(60),
      reservedConcurrentExecutions: 1,
      retryAttempts: 0, // a failed render costs Cost Explorer calls; the next one is 4 h away anyway
      environment: { BUCKET: bucket.bucketName },
    });
    renderer.addToRolePolicy(new PolicyStatement({
      // Cost Explorer has no resource-level permissions. Its endpoint is us-east-1 (see collect.py).
      actions: ['ce:GetCostAndUsage', 'ce:GetCostForecast', 'ce:GetAnomalies', 'cloudwatch:GetMetricData'],
      resources: ['*'],
    }));
    renderer.addToRolePolicy(new PolicyStatement({
      actions: ['budgets:ViewBudget'],
      // Pseudo parameters, not this.account: keeps the template free of the account ID.
      resources: [`arn:${Aws.PARTITION}:budgets::${Aws.ACCOUNT_ID}:budget/*`],
    }));
    renderer.addToRolePolicy(new PolicyStatement({
      actions: ['s3:PutObject'],
      resources: [bucket.arnForObjects('latest.png'), bucket.arnForObjects('latest.json')],
    }));
    Validations.of(renderer.role!).acknowledge(
      { id: 'AwsSolutions::AwsSolutions-IAM5[Resource::*]', reason: 'Cost Explorer and GetMetricData do not support resource-level permissions; actions are read-only.' },
      { id: `AwsSolutions::AwsSolutions-IAM5[Resource::arn:<AWS::Partition>:budgets::<AWS::AccountId>:budget/*]`, reason: 'Reads every cost budget of the account; ViewBudget is read-only.' },
    );

    const schedulerRole = new Role(this, 'SchedulerRole', { assumedBy: new ServicePrincipal('scheduler.amazonaws.com') });
    new CfnOutput(this, 'RendererName', { value: renderer.functionName, description: 'make render runs it once' });
    new Schedule(this, 'RenderSchedule', {
      description: 'Render the Kindle dashboard a few minutes before the Kindle fetches it',
      schedule: ScheduleExpression.cron({ minute: '0', hour: RENDER_HOURS, timeZone: TimeZone.EUROPE_ROME }),
      target: new LambdaInvoke(renderer, { role: schedulerRole, retryAttempts: 2, maxEventAge: Duration.hours(1) }),
    });
    Validations.of(schedulerRole).acknowledge({
      id: `AwsSolutions::AwsSolutions-IAM5[Resource::<${this.getLogicalId(renderer.node.defaultChild as CfnElement)}.Arn>:*]`,
      reason: 'lambda:InvokeFunction on the renderer and its versions/aliases, as granted by CDK.',
    });

    const serve = this.pythonFunction('Serve', {
      code: Code.fromAsset(join(LAMBDAS, 'serve'), { exclude: ['__pycache__'] }),
      memorySize: 256,
      timeout: Duration.seconds(10),
      // Caps what anyone hitting the public URL can run up; the Kindle makes one request per wake-up.
      reservedConcurrentExecutions: 2,
      environment: { BUCKET: bucket.bucketName, TOKEN_PARAMETER: props.tokenParameterName },
    });
    serve.addToRolePolicy(new PolicyStatement({
      actions: ['s3:GetObject'],
      resources: [bucket.arnForObjects('latest.png')],
    }));
    serve.addToRolePolicy(new PolicyStatement({
      // SecureString encrypted with the AWS managed key aws/ssm: its key policy allows decryption
      // through SSM for the account, so no kms:Decrypt statement is needed.
      actions: ['ssm:GetParameter'],
      resources: [this.formatArn({ service: 'ssm', resource: 'parameter', resourceName: props.tokenParameterName.replace(/^\//, '') })],
    }));
    const url = serve.addFunctionUrl({ authType: FunctionUrlAuthType.NONE });
    new CfnOutput(this, 'DashboardUrl', { value: url.url, description: 'DASH_URL for the Kindle env.sh' });

    const alerts = new Topic(this, 'Alerts', { enforceSSL: true });
    Validations.of(alerts).acknowledge({
      id: 'AwsSolutions::AwsSolutions-SNS2',
      reason: 'CloudWatch alarms cannot publish to a topic encrypted with the AWS managed aws/sns key, and a customer managed key costs $1/month for a non-sensitive low-battery notice.',
    });
    if (props.alarmEmail) {
      alerts.addSubscription(new EmailSubscription(props.alarmEmail));
    } else {
      Annotations.of(this).addWarningV2('kindle:noAlarmEmail', 'No alarmEmail: the low-battery alarm has no subscriber.');
    }

    const lowBattery = new Alarm(this, 'KindleLowBattery', {
      alarmDescription: 'Kindle battery below 15%: charge it.',
      // Published by the serve Lambda (Embedded Metric Format) on every authorised fetch.
      metric: new Metric({ namespace: 'KindleDashboard', metricName: 'Battery', statistic: 'Minimum', period: Duration.hours(1) }),
      threshold: 15,
      comparisonOperator: ComparisonOperator.LESS_THAN_THRESHOLD,
      evaluationPeriods: 1,
      treatMissingData: TreatMissingData.NOT_BREACHING,
    });
    lowBattery.addAlarmAction(new SnsAction(alerts));
  }

  /** Python 3.14 on Graviton, with its own role and log group: no AWS managed policies. */
  private pythonFunction(id: string, props: Omit<FunctionProps, 'runtime' | 'handler'>): Function {
    const logGroup = new LogGroup(this, `${id}Logs`, {
      retention: RetentionDays.ONE_MONTH,
      removalPolicy: RemovalPolicy.DESTROY,
    });
    const role = new Role(this, `${id}Role`, { assumedBy: new ServicePrincipal('lambda.amazonaws.com') });
    logGroup.grantWrite(role);
    Validations.of(role).acknowledge({
      id: `AwsSolutions::AwsSolutions-IAM5[Resource::<${this.getLogicalId(logGroup.node.defaultChild as CfnElement)}.Arn>:*]`,
      reason: 'Log streams of this function\'s own log group; stream names are generated by Lambda.',
    });
    return new Function(this, id, {
      ...props,
      runtime: Runtime.PYTHON_3_14,
      architecture: Architecture.ARM_64,
      handler: 'handler.handler',
      role,
      logGroup,
    });
  }
}

/**
 * Pillow for Lambda arm64 without Docker: uv downloads the manylinux aarch64 wheels on any OS.
 * manylinux_2_28 is compatible with Lambda's Amazon Linux 2023 (glibc 2.34). Docker is only the
 * fallback when uv isn't installed.
 */
function rendererBundling() {
  const pipArgs = '--only-binary :all: --require-hashes -r requirements.txt';
  return {
    image: Runtime.PYTHON_3_14.bundlingImage,
    platform: 'linux/arm64',
    command: ['bash', '-c', `pip install ${pipArgs} -t /asset-output && cp -au . /asset-output`],
    local: {
      tryBundle(outputDir: string) {
        const src = join(LAMBDAS, 'renderer');
        try {
          execFileSync('uv', [
            'pip', 'install', '--quiet', '--python-platform', 'aarch64-manylinux_2_28', '--python-version', '3.14',
            ...pipArgs.split(' '), '--target', outputDir,
          ], { cwd: src, stdio: 'inherit' });
        } catch {
          return false;
        }
        cpSync(src, outputDir, { recursive: true, filter: (p) => !p.includes('__pycache__') });
        return true;
      },
    },
  };
}
