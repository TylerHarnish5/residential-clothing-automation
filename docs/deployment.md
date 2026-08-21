# AWS Deployment Preparation (Milestone 10)

This is a deliberately small, low-traffic portfolio deployment. It uses one
Amazon EC2 instance for the Dockerized FastAPI application and one private,
single-AZ Amazon RDS for PostgreSQL instance. It does **not** create a load
balancer, NAT Gateway, Kubernetes cluster, container registry, multi-AZ
database, or public database.

This document prepares the project only. Nothing in the repository provisions
or purchases AWS resources. Create each resource yourself in the AWS Console
only after reviewing its estimated monthly cost and your remaining AWS credits.

## Architecture

```text
Internet
    |
    | HTTP :80 (portfolio demonstration only)
    v
EC2 t3.micro in default VPC public subnet
    |  Docker: FastAPI application on container port 8000
    |  Docker logs and EC2/RDS console metrics
    |
    | PostgreSQL :5432, allowed only by the RDS security group
    v
RDS PostgreSQL db.t3.micro, Single-AZ, not publicly accessible
```

Use the default VPC in one AWS Region for this V0 deployment. It already has
an Internet Gateway, so a public EC2 instance can receive visitors and download
Docker packages without a NAT Gateway. The database remains private because its
security group permits port 5432 only from the EC2 security group.

The application uses [compose.aws.yaml](../compose.aws.yaml), which is separate
from the local [compose.yaml](../compose.yaml). The local setup continues to
run its own PostgreSQL container on port 5433; the AWS setup never does.

## Cost and Free Plan checks -- do these first

AWS account benefits now depend on when and how the account was created. Newer
accounts can use a credit-based Free Plan that ends after six months or when
credits run out; it is not a promise that every selected service costs zero.
Before creating anything, open **AWS Console -> Billing and Cost Management**
and check **Explore AWS**, your account plan, remaining credits, and the
estimate shown on each creation page. AWS documents the current plan behavior
in its [Free Plan guide](https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/free-tier-plans.html).

Create a monthly **AWS Budget** with an email alert before deploying. Select the
console's Free Tier / credit alert options as well. Budget data is delayed, so
an alert is a warning, not an immediate shutdown switch.

| Resource | V0 selection | Cost warning to verify in the console |
| --- | --- | --- |
| EC2 | `t3.micro`, Amazon Linux 2023, one instance | Compute, EBS storage, and transfer can consume credits or be billed after eligibility ends. Choose an AMI and instance type visibly marked Free Plan/Free Tier eligible in your Region. |
| EC2 public IPv4 | One auto-assigned address; no Elastic IP | AWS lists public IPv4 at `$0.005/hour` (about `$3.60/month` if left running). Verify whether your credits cover it. Do not allocate an Elastic IP. |
| EBS | One `gp3` root volume, 8 GiB | Storage can be billed. Select the smallest practical size and delete it when the instance is terminated if asked. |
| RDS PostgreSQL | `db.t3.micro`, Single-AZ, 20 GiB `gp3`, one-day backup retention | RDS instance hours, storage, and extra backup storage can consume credits or become billable. Select **Free tier** only if the console presents it for your account; otherwise review the estimate before continuing. |
| RDS subnet group / security groups / IAM role | Use defaults or create only the two security groups and one EC2 role described below | These normally do not have a direct hourly charge, but do not add NAT Gateway, load balancer, Elastic IP, read replica, Performance Insights paid retention, or CloudWatch Logs ingestion. |

AWS currently documents `t3.micro` as an eligible choice for newer EC2 Free
Plan accounts and `db.t3.micro` / `db.t4g.micro` for supported RDS engines, but
availability and credits vary by account and Region. Verify this in the console
at creation time rather than relying on this document. References: [EC2 Free
Tier guidance](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-free-tier-usage.html),
[RDS Free Tier guidance](https://aws.amazon.com/rds/free/), and [public IPv4
pricing](https://aws.amazon.com/vpc/pricing/).

## AWS resources to create, in order

Keep every resource in the same Region. Name each with a recognisable prefix,
such as `residential-clothing-v0`, so it is easy to find and remove later.

1. **Budget and billing alerts.** In Billing and Cost Management, turn on Free
   Tier/credit alerts, then create a monthly cost budget with your chosen small
   dollar threshold and your email address. The exact threshold is your choice;
   use one lower than your remaining credits.
2. **EC2 security group.** Create `residential-clothing-app-sg` in the default
   VPC. Add one inbound rule: HTTP/TCP port 80 from `0.0.0.0/0`. Do not open
   SSH port 22. Leave the default outbound rule so the instance can reach RDS,
   package repositories, and Systems Manager.
3. **RDS security group.** Create `residential-clothing-db-sg` in the same VPC.
   Add one inbound rule: PostgreSQL/TCP port 5432 with source set to
   `residential-clothing-app-sg` (select the security group, not an IP range).
   Do not add `0.0.0.0/0` and do not make this database publicly accessible.
4. **EC2 IAM role.** Create a role for EC2 named
   `residential-clothing-ec2-role` and attach the AWS managed policy
   `AmazonSSMManagedInstanceCore`. This permits Session Manager access without
   opening SSH. Do not attach administrator permissions. Basic app logging stays
   in Docker's local logs, so no CloudWatch agent or CloudWatch Logs permission
   is required for V0.
5. **RDS database.** In RDS, choose **Create database**, PostgreSQL, and the
   Free tier template only when it is shown as available. Choose:

   - DB instance identifier: `residential-clothing-v0-db`
   - Master username: a new non-personal name such as `app_admin`
   - Credentials: choose and save a strong password in your password manager;
     do not paste it into Git, a source file, or a screenshot.
   - Initial database name: `residential_clothing`
   - Instance class: `db.t3.micro` (or the smallest PostgreSQL option explicitly
     eligible for your account)
   - Storage: 20 GiB General Purpose SSD (`gp3`), storage autoscaling off
   - Availability & durability: Single-AZ; no read replica
   - Connectivity: default VPC, `residential-clothing-db-sg`, **Public access:
     No**
   - Additional configuration: backup retention one day; disable options that
     add cost if offered, such as paid Performance Insights retention

   Wait until the DB status is **Available**, then copy its endpoint. The
   endpoint is a hostname such as `name.random.region.rds.amazonaws.com`; do not
   include `https://`.
6. **EC2 instance.** In EC2, launch one instance with Amazon Linux 2023 and
   `t3.micro` if it is marked eligible in your account/Region. Attach
   `residential-clothing-app-sg` and `residential-clothing-ec2-role`. Enable an
   auto-assigned public IPv4 address (it is needed for the simple HTTP portfolio
   demonstration and has the charge warning above). Use an 8 GiB `gp3` root
   volume. A key pair is optional because Session Manager is the intended access
   method; do not open port 22 merely to use SSH.

## Application secrets and RDS connection

For V0, store the deployment environment file on the EC2 host, outside the Git
checkout, rather than paying for another secret-management service. The file is
read by Docker Compose but is never copied into the image because it lives
outside the repository. `app.env` is also ignored by Git if you make a local
copy by mistake.

Connect to EC2 using **Systems Manager -> Session Manager -> Start session**.
On the Amazon Linux 2023 instance, install Docker and Git, enable Docker, and
give the standard `ec2-user` permission to use it:

```bash
sudo yum update -y
sudo yum install -y docker git
sudo systemctl enable --now docker
sudo usermod -a -G docker ec2-user
exit
```

Close that Session Manager session, start a fresh session, then verify Docker
and the Compose plugin before continuing:

```bash
docker version
docker compose version
```

If `docker compose version` is unavailable, stop here and install the current
Compose plugin using Docker's official instructions for Amazon Linux rather than
installing an unreviewed third-party script. Create the environment file with
an editor that does not record it in shell history:

```bash
sudo mkdir -p /opt/residential-clothing
sudo chown ec2-user:ec2-user /opt/residential-clothing
nano /opt/residential-clothing/app.env
```

Put only these values in that file, replacing every placeholder yourself:

```dotenv
DATABASE_URL=postgresql+psycopg://app_admin:REPLACE_WITH_YOUR_RDS_PASSWORD@REPLACE_WITH_RDS_ENDPOINT:5432/residential_clothing?sslmode=require
LOG_LEVEL=INFO
```

Then restrict access:

```bash
chmod 600 /opt/residential-clothing/app.env
```

`sslmode=require` encrypts the PostgreSQL connection in transit. For a future
production deployment, use RDS CA certificate validation (`verify-full`) and a
dedicated application database role with least privileges. Those hardening
steps are intentionally outside this low-traffic V0 milestone.

## Deploy and migrate safely

The AWS Compose configuration has two services: `migrate` exits after Alembic
finishes, while `app` starts the API. This keeps schema changes explicit instead
of silently applying them every time the app restarts.

In the EC2 Session Manager shell, clone the repository into the same directory
as the secret file. Replace the branch with the branch that contains this
Milestone 10 work after you push it to GitHub:

```bash
cd /opt/residential-clothing
git clone https://github.com/TylerHarnish5/residential-clothing-automation.git app
cd app
git switch YOUR_MILESTONE_10_BRANCH
docker compose -f compose.aws.yaml build
docker compose -f compose.aws.yaml run --rm migrate
docker compose -f compose.aws.yaml up -d app
docker compose -f compose.aws.yaml ps
```

Before `up -d app`, the `migrate` command must exit with code 0. If it fails,
do not start or update the application: inspect the output, correct the database
endpoint/security-group/credential issue, and rerun it. Alembic records applied
migrations, so rerunning this command is safe when there are no new migrations.

For a later application update, use this order:

```bash
cd /opt/residential-clothing/app
git pull
docker compose -f compose.aws.yaml build
docker compose -f compose.aws.yaml run --rm migrate
docker compose -f compose.aws.yaml up -d --no-deps app
```

## Verify and inspect

Find the EC2 instance's **Public IPv4 DNS** or **Public IPv4 address** in the
EC2 console. In your browser, visit:

```text
http://PUBLIC_EC2_ADDRESS/health
http://PUBLIC_EC2_ADDRESS/docs
```

`/health` should return `{"status":"ok"}` and `/docs` should show FastAPI's
interactive API documentation. This V0 path is HTTP-only. Do not describe it as
a production HTTPS deployment.

The application already writes structured request-completion and workflow logs
to standard output. Inspect the bounded local Docker logs through Session
Manager:

```bash
cd /opt/residential-clothing/app
docker compose -f compose.aws.yaml logs --tail=200 app
docker compose -f compose.aws.yaml logs -f app
docker compose -f compose.aws.yaml ps
```

For basic AWS monitoring, check EC2 **Status checks** and the RDS database
**Status** and **Monitoring** tabs. Do not add CloudWatch custom metrics,
alarms, or log ingestion until you have checked their pricing. If you later
choose CloudWatch Logs, add only the least-privilege IAM permissions and first
review its ingestion/retention cost.

## Stop or remove everything

Stopping EC2 stops compute charges but RDS continues to incur storage charges
while it exists. For a short pause, stop both services and make a note to
recheck them before the RDS maximum stop period ends:

1. In EC2, select the instance and choose **Instance state -> Stop instance**.
2. In RDS, select the database and choose **Actions -> Stop temporarily** if
   the console allows it. RDS storage and backup charges may still apply.

For a complete teardown when you are finished with the portfolio demonstration:

1. In RDS, select the database and choose **Delete**. Decide whether you need a
   final snapshot; a retained snapshot can generate storage charges. For a
   disposable demo database, decline the final snapshot only after confirming
   that losing the data is acceptable. Delete any manual snapshots afterward.
2. In EC2, terminate the instance and confirm the root EBS volume is deleted.
   Do not retain or allocate an Elastic IP.
3. Delete `residential-clothing-db-sg` and `residential-clothing-app-sg` once
   no resource references them. Delete the EC2 IAM role if it will not be reused.
4. In Billing and Cost Management, open **Bills**, **Cost Explorer**, **Free
   Tier**, and **Budgets**. Filter by service/Region and confirm there are no
   running EC2 instances, RDS instances, EBS volumes or snapshots, public IPv4
   addresses, NAT Gateways, load balancers, or paid log groups consuming credits.

Billing data is delayed. Check again on the following day; AWS also recommends
Free Tier alerts and Budgets for ongoing monitoring. See [AWS Free Tier usage
tracking](https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/tracking-free-tier-usage.html)
and [AWS Budgets guidance](https://docs.aws.amazon.com/cost-management/latest/userguide/budgets-managing-costs.html).

## V0 boundary and unresolved decisions

- This deployment has no custom domain, TLS/HTTPS, authentication, load
  balancer, auto-scaling, backup/restore runbook, container registry, or
  automated deployment pipeline. Those are deliberately deferred.
- Publishing the operational interface over plain HTTP is suitable only for a
  temporary portfolio demo with non-sensitive sample data. Before real resident
  information or public use, decide on authentication, HTTPS, a domain, and the
  appropriate privacy/compliance requirements.
- The current data model has one RDS master/application credential. Before a
  multi-user or production deployment, decide whether to create a limited
  application role and move the secret to a managed service after reviewing its
  cost.
