# Deploying the SQL Agent chat app to AWS

The app is one Docker container. In production it reaches Bedrock through an **IAM role**,
so no API key is stored anywhere. Commands below work in **PowerShell** on Windows (and in bash).

```
Browser ──HTTPS──▶ AWS App Runner (container, Basic-auth login) ──IAM role──▶ Bedrock (eu-north-1)
```

> **Region note:** App Runner isn't offered in `eu-north-1` (Stockholm), so host the app in
> **`eu-west-1` (Ireland)** and keep calling Bedrock in `eu-north-1` with `BEDROCK_REGION`.
> If App Runner isn't available to your account, use the ECS Fargate option at the end.

## 0. Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) running.
- [AWS CLI v2](https://aws.amazon.com/cli/) set up with an IAM user/SSO profile that can use ECR, IAM and App Runner.
  The Bedrock API key is **not** enough for this step; run `aws configure` (or `aws configure sso`).
- Qwen and gpt-oss models enabled under **Bedrock → Model access** in `eu-north-1`.

Check the CLI works:

```powershell
aws sts get-caller-identity
```

## 1. Push the image to Amazon ECR

Run these from the project folder (`amazon-bedrock-main`).

```powershell
$ACCOUNT = aws sts get-caller-identity --query Account --output text
```
```powershell
$REGION = "eu-west-1"
```
```powershell
$REPO = "$ACCOUNT.dkr.ecr.$REGION.amazonaws.com/sql-agent"
```
```powershell
aws ecr create-repository --repository-name sql-agent --region $REGION
```
```powershell
aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin "$ACCOUNT.dkr.ecr.$REGION.amazonaws.com"
```
```powershell
docker build --platform linux/amd64 -t sql-agent .
```
```powershell
docker tag sql-agent "${REPO}:latest"
```
```powershell
docker push "${REPO}:latest"
```

## 2. Create the two IAM roles

**Instance role:** what the running app uses to call Bedrock.

```powershell
aws iam create-role --role-name SqlAgentInstanceRole --assume-role-policy-document file://deploy/apprunner-trust-policy.json
```
```powershell
aws iam put-role-policy --role-name SqlAgentInstanceRole --policy-name BedrockInvoke --policy-document file://deploy/bedrock-invoke-policy.json
```

**ECR access role:** lets App Runner pull your image.

```powershell
aws iam create-role --role-name SqlAgentEcrAccessRole --assume-role-policy-document file://deploy/apprunner-ecr-access-trust-policy.json
```
```powershell
aws iam attach-role-policy --role-name SqlAgentEcrAccessRole --policy-arn arn:aws:iam::aws:policy/service-role/AWSAppRunnerServicePolicyForECRAccess
```

## 3. Create the App Runner service (console)

In the AWS console, switch to **Europe (Ireland) eu-west-1** and open **App Runner → Create service**.

1. **Source:** Container registry → Amazon ECR → browse to `sql-agent:latest`.
   **Deployment:** Automatic (every `docker push` redeploys). **ECR access role:** `SqlAgentEcrAccessRole`.
2. **Service settings:**
   - Port: `8080`
   - Environment variables:
     | Name | Value |
     |---|---|
     | `BEDROCK_REGION` | `eu-north-1` |
     | `APP_USERNAME` | a username of your choice |
     | `APP_PASSWORD` | a long random password. Better: store it in Secrets Manager and reference it here |
     | `SQL_AGENT_MODEL` | `gpt` or `qwen` (default model in the menu) |
   - **Security → Instance role:** `SqlAgentInstanceRole`
3. **Health check:** HTTP, path `/health`.
4. **Create & deploy**. After a few minutes you get a URL like `https://xxxx.eu-west-1.awsapprunner.com`.
   Open it, log in with `APP_USERNAME`/`APP_PASSWORD`, and chat.

To ship a new version later, re-run the `docker build`, `docker tag` and `docker push` lines from step 1.

## Production checklist

- **Always set `APP_USERNAME` and `APP_PASSWORD`.** Without them anyone with the URL can use the app and spend
  your Bedrock budget. For company-wide sign-in, put the app behind Amazon Cognito or your SSO
  (e.g. an ALB with OIDC auth), then the Basic-auth variables can be dropped.
- **Use a real read-only database account** if you point it at your own database (see "Your own data" below).
- **Set a budget:** AWS Billing → Budgets, with an alert on Bedrock spend.
- **Logs:** App Runner sends container logs to CloudWatch automatically.
- **Chat memory** is kept in the container's memory: it resets on deploys and isn't shared between
  instances. Keep App Runner at 1 instance (max concurrency ~50) or move sessions to DynamoDB/Redis
  before scaling out.
- **Timeouts:** App Runner cuts requests at 120 seconds. Hard questions usually take 10–60 s; if you see
  cut-offs, lower `max_iterations` in `SQLAgent` or switch to ECS behind an ALB (configurable timeout).

## Your own data

The sample SQLite file is built into the image. To use real data you can:
- **Swap the SQLite file:** set `SQL_AGENT_DB` to your file's path and add it to the image (`COPY mydata.db /app/data/`).
- **Use PostgreSQL/MySQL (e.g. Amazon RDS):** implement the same four methods as `Database` in
  `sql_agent/database.py` (`list_tables`, `describe_table`, `schema_overview`, `run_query`) with a
  driver such as `psycopg`, connect as a **read-only** DB user, and keep the `SELECT`-only check.

## Alternative: ECS Fargate

If App Runner isn't available to you, run the same image on **ECS Fargate** behind an
**Application Load Balancer**: create a cluster, a task definition using the ECR image (port 8080, the
same environment variables, `SqlAgentInstanceRole` as the **task role** with its trust policy changed to
`ecs-tasks.amazonaws.com`), and a service with an ALB listener (target health check path `/health`;
raise the ALB idle timeout to 180 s). You can run it in `eu-north-1` directly.
