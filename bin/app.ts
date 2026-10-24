import { App, Validations } from 'aws-cdk-lib';
import { AwsSolutionsChecks } from 'cdk-nag';
import { DashboardStack } from '../lib/dashboard-stack.ts';

const app = new App();
// Deploy-specific values come from context or env at deploy time; nothing account-specific is committed:
//   npx cdk deploy -c alarmEmail=you@example.com
new DashboardStack(app, 'KindleFinopsDashboard', {
  // Account-agnostic (no lookups): the account comes from the deploy credentials.
  env: { region: 'eu-west-1' },
  alarmEmail: app.node.tryGetContext('alarmEmail') ?? process.env.ALARM_EMAIL,
  tokenParameterName: app.node.tryGetContext('tokenParameter') ?? '/kindle-dashboard/token',
});
Validations.of(app).addPlugins(new AwsSolutionsChecks(app, { verbose: true }));
