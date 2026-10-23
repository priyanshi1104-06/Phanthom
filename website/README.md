# Phantom website

One page, plain HTML and CSS, no JavaScript and no outside services: fonts and images are served from the site
itself.

| Path | What it is |
|---|---|
| `src/` | the page (`index.html`), the 404 page, `styles.css`, fonts, icons, the share image (`assets/og.png`) |
| `site.json` | your details: the site's address, the contact email, the VS Code Marketplace link |
| `build.py` | fills the details into `src/` and writes `dist/` (the files that get uploaded) |
| `infra/phantom-website.yaml` | everything on AWS, as one CloudFormation template |
| `../.github/workflows/website.yml` | checks every pull request; publishes when the site changes on `main` |

## Look at it on your laptop

```bash
python3 website/build.py
python3 -m http.server --directory website/dist 8000
```

Then open http://localhost:8000. Dark or light follows your system setting.

## Put it online (once, about 30 minutes)

You do steps 1 to 5 in your browser; they need your own accounts, so no one else can do them.

1. **AWS account.** Sign up at aws.amazon.com (it asks for a card). Then, in the account menu, *Security
   credentials*: turn on **MFA** for the root user. An AWS account without MFA is a common way to get a huge bill.
2. **A domain (optional now).** You can start without one and get an address like
   `https://d1abcd2efgh3.cloudfront.net`. To use your own: AWS console, **Route 53 > Registered domains > Register
   domains**. After it's registered, *Hosted zones* lists it; copy its **Hosted zone ID** (starts with `Z`).
3. **Create the stack.** In the AWS console, set the region (top right) to **US East (N. Virginia)**. Open
   **CloudFormation > Create stack > With new resources**, choose *Upload a template file* and pick
   `website/infra/phantom-website.yaml`. Stack name: `phantom-website`. Parameters:
   - `DomainName` and `HostedZoneId`: from step 2, or leave both empty;
   - `AlertEmail`: your email, for the budget alarm (it covers your whole AWS account);
   - `MonthlyBudgetUsd`: `10`;
   - leave the GitHub ones as they are.

   Tick *I acknowledge that AWS CloudFormation might create IAM resources*, then **Submit**. It takes 5 to 15
   minutes (up to 30 with a domain, while the certificate is issued). AWS then emails you to confirm the budget
   alarm.
4. **Copy the outputs.** The stack's **Outputs** tab shows `SiteUrl`, `BucketName`, `DistributionId` and
   `DeployRoleArn`.
5. **Tell GitHub where to publish.** In the Phantom repository on GitHub: **Settings > Secrets and variables >
   Actions > Variables > New repository variable**, three times:

   | Name | Value |
   |---|---|
   | `AWS_DEPLOY_ROLE_ARN` | `DeployRoleArn` from the outputs |
   | `SITE_BUCKET` | `BucketName` |
   | `SITE_DISTRIBUTION_ID` | `DistributionId` |

   These are not secrets: GitHub gets short-lived AWS credentials each time instead of stored keys, and only this
   repository's `main` branch can use the role.
6. **Fill in `site.json`**: `site_url` is `SiteUrl` (for example `https://phantom.example`, no slash at the end),
   `contact_email` is the address people write to for early access. It will be public on the page, so use one
   made for Phantom, not your personal one. Leave `marketplace_url` empty until the extension is on the VS Code
   Marketplace; when it's set, the main button becomes "Install for VS Code".
7. **Merge to `main`.** The *Website* workflow builds the site and publishes it (GitHub, *Actions* tab). Open
   `SiteUrl`.

After that, every change to `website/` that reaches `main` is live about two minutes later.

## What it costs

For a new site: S3 storage is cents, CloudFront's free tier covers 1 TB and 10 million requests a month, the
certificate is free. Expect under $1 a month, plus $0.50 a month for a Route 53 hosted zone and the domain's yearly
price if you use one. The budget alarm emails you at 80% of `MonthlyBudgetUsd`.

## How it's built

- **S3** keeps the files: private, encrypted, versioned (old versions are kept 30 days, so a bad publish can be
  rolled back). Only this CloudFront distribution can read it.
- **CloudFront** serves the site from locations around the world, HTTPS only, with security headers (HSTS, a
  strict Content-Security-Policy, no framing). A small CloudFront function sends `www.` to the bare domain.
- **ACM** issues the HTTPS certificate for the domain and renews it; **Route 53** points the domain at CloudFront.
- **IAM**: GitHub's OIDC provider and a role that can only upload to this bucket and refresh this distribution.
- To remove everything: delete the stack in CloudFormation. The bucket is kept on purpose (so a delete can't lose
  the site); empty it and delete it by hand if you want it gone.
