import { test } from 'node:test';
import assert from 'node:assert/strict';
import { App, Validations } from 'aws-cdk-lib';
import { Match, Template } from 'aws-cdk-lib/assertions';
import { AwsSolutionsChecks } from 'cdk-nag';
import { DashboardStack } from '../lib/dashboard-stack.ts';

function synth(alarmEmail?: string) {
  // No bundling in tests: offline, fast, and the snapshot doesn't depend on the wheels.
  const app = new App({ context: { 'aws:cdk:bundling-stacks': [] } });
  const stack = new DashboardStack(app, 'Test', {
    env: { region: 'eu-west-1' }, // account-agnostic, like a deploy without lookups
    tokenParameterName: '/kindle-dashboard/token',
    alarmEmail,
  });
  Validations.of(app).addPlugins(new AwsSolutionsChecks(app));
  app.synth(); // throws on any unacknowledged cdk-nag finding
  return Template.fromStack(stack);
}

const template = synth('alerts@example.com');
const json = template.toJSON();

/** Statements of the inline policy attached to the role whose logical ID starts with prefix. */
function statements(rolePrefix: string) {
  const policies = Object.values(template.findResources('AWS::IAM::Policy'))
    .filter((p) => JSON.stringify(p.Properties.Roles).includes(rolePrefix));
  assert.equal(policies.length, 1);
  return policies[0].Properties.PolicyDocument.Statement as { Action: string | string[]; Resource: unknown }[];
}
const actions = (rolePrefix: string) => statements(rolePrefix).flatMap((s) => [s.Action].flat()).sort();

test('snapshot', (t) => {
  // Asset hashes change with every Python edit: normalise them so the snapshot shows real changes.
  const normalised = JSON.parse(JSON.stringify(json).replace(/[0-9a-f]{64}(\.zip)?/g, 'ASSET$1'));
  t.assert.snapshot(normalised);
});

test('bucket is private, encrypted, TLS-only and retained', () => {
  template.hasResource('AWS::S3::Bucket', {
    DeletionPolicy: 'Retain',
    Properties: {
      PublicAccessBlockConfiguration: {
        BlockPublicAcls: true, BlockPublicPolicy: true, IgnorePublicAcls: true, RestrictPublicBuckets: true,
      },
      BucketEncryption: { ServerSideEncryptionConfiguration: [{ ServerSideEncryptionByDefault: { SSEAlgorithm: 'AES256' } }] },
    },
  });
  template.hasResourceProperties('AWS::S3::BucketPolicy', {
    PolicyDocument: {
      Statement: Match.arrayWith([Match.objectLike({ Effect: 'Deny', Condition: { Bool: { 'aws:SecureTransport': 'false' } } })]),
    },
  });
});

test('Function URL is public; auth is the bearer token in the function', () => {
  template.resourceCountIs('AWS::Lambda::Url', 1);
  template.hasResourceProperties('AWS::Lambda::Url', { AuthType: 'NONE' });
  // The public InvokeFunction grant only applies through the URL, not to direct Invoke calls.
  template.hasResourceProperties('AWS::Lambda::Permission', {
    Action: 'lambda:InvokeFunction', Principal: '*', InvokedViaFunctionUrl: true,
  });
});

test('functions: Python 3.14 on arm64, capped concurrency, logs kept 30 days', () => {
  const fns = Object.values(template.findResources('AWS::Lambda::Function'));
  assert.equal(fns.length, 2);
  for (const f of fns) {
    assert.equal(f.Properties.Runtime, 'python3.14');
    assert.deepEqual(f.Properties.Architectures, ['arm64']);
    assert.ok(f.Properties.ReservedConcurrentExecutions <= 2);
  }
  const logs = Object.values(template.findResources('AWS::Logs::LogGroup'));
  assert.equal(logs.length, 2);
  for (const l of logs) assert.equal(l.Properties.RetentionInDays, 30);
});

test('IAM is least privilege', () => {
  for (const role of Object.values(template.findResources('AWS::IAM::Role'))) {
    assert.equal(role.Properties.ManagedPolicyArns, undefined, 'no AWS managed policies');
  }
  assert.deepEqual(actions('ServeRole'), ['logs:CreateLogStream', 'logs:PutLogEvents', 's3:GetObject', 'ssm:GetParameter']);
  assert.deepEqual(actions('RendererRole'), [
    'budgets:ViewBudget', 'ce:GetAnomalies', 'ce:GetCostAndUsage', 'ce:GetCostForecast', 'cloudwatch:GetMetricData',
    'logs:CreateLogStream', 'logs:PutLogEvents', 's3:PutObject',
  ]);
  // The serve Lambda reads one object and one parameter, nothing else.
  const serveResources = JSON.stringify(statements('ServeRole').map((s) => s.Resource));
  assert.match(serveResources, /latest\.png/);
  assert.doesNotMatch(serveResources, /latest\.json|"\*"/);
  assert.match(serveResources, /parameter\/kindle-dashboard\/token/);
});

test('renders on a Europe/Rome schedule', () => {
  template.hasResourceProperties('AWS::Scheduler::Schedule', {
    ScheduleExpression: 'cron(0 7,11,15,19 * * ? *)',
    ScheduleExpressionTimezone: 'Europe/Rome',
  });
});

test('low-battery alarm emails the address given at deploy time', () => {
  template.hasResourceProperties('AWS::CloudWatch::Alarm', {
    Namespace: 'KindleDashboard', MetricName: 'Battery', Threshold: 15, ComparisonOperator: 'LessThanThreshold',
  });
  template.hasResourceProperties('AWS::SNS::Subscription', { Protocol: 'email', Endpoint: 'alerts@example.com' });
  Template.fromJSON(synth().toJSON()).resourceCountIs('AWS::SNS::Subscription', 0);
});

test('no account ID in the template', () => {
  assert.doesNotMatch(JSON.stringify(json), /\d{12}/);
});
