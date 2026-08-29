// Copy this file to config.js and fill in the values from the CloudFormation
// stack outputs (`sam deploy` prints them, or `aws cloudformation describe-stacks`).
window.ISTO_DEMO_CONFIG = {
  apiUrl: "https://REPLACE.execute-api.REPLACE.amazonaws.com/dev", // Outputs.ApiUrl
  region: "us-east-1", // Outputs.Region
  userPoolClientId: "REPLACE", // Outputs.UserPoolClientId
};
