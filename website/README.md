# Phantom website

Plain HTML and CSS, two small scripts (the intro and Google sign-in), no outside services but sign-in: fonts and
images are served from the site itself.

| Path | What it is |
|---|---|
| `src/` | the pages (`index.html`, `signin.html`, `account.html`, `privacy.html`, 404), `styles.css`, fonts, icons, scripts |
| `site.json` | your details: the site's address, the contact email, the VS Code Marketplace link, the sign-in service |
| `build.py` | fills the details into `src/`, adds the newest `extension/phantom-*.vsix` for download, writes `dist/` |
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

After that, every change to `website/` or `extension/` that reaches `main` is live about two minutes later. Each
publish packages the extension and puts it on the account page for download.

## Turn on Google sign-in and early access (once, about 30 minutes)

How it works: **Get Phantom for VS Code** opens the sign-in page; **Continue with Google** goes through Amazon
Cognito, which keeps the list of everyone who signed in. On their first sign-in Phantom emails them
"You're in: Phantom early access" (Amazon SES), and they land on the account page: a download button and three
steps to get Phantom running in VS Code. Early access is open to everyone who signs in.

1. **Pick a sign-in address prefix**, for example `phantom-ai-login`. Your sign-in service will live at
   `https://PREFIX.auth.us-east-1.amazoncognito.com` (the prefix must be unused across all of AWS).
2. **Google Cloud: the consent screen.** Sign in at console.cloud.google.com, create a project called `Phantom`,
   then open **Google Auth Platform** (search "OAuth consent screen") > **Get started**: app name `Phantom`, your
   support email, audience **External**, contact email, agree, **Create**. Under **Branding** add your home page
   `SITE_URL`, privacy policy `SITE_URL/privacy.html`, and authorized domains `amazoncognito.com` and your own
   domain. Leave the logo empty for now: a logo makes Google review the branding first (days). Under **Audience**,
   **Publish app** so anyone can sign in, not only test users. Scopes stay `openid`, `email`, `profile`
   (nothing sensitive, so no review).
3. **Google Cloud: the client.** **Google Auth Platform > Clients > Create client**, type **Web application**,
   name `Phantom website`, *Authorized redirect URIs*:
   `https://PREFIX.auth.us-east-1.amazoncognito.com/oauth2/idpresponse`. Copy the **Client ID** and **Client
   secret** (keep the secret out of git, chat and email: it goes only into the next steps).
4. **Amazon SES, for the confirmation email.** AWS console, region US East (N. Virginia), **SES > Identities >
   Create identity**: your domain (best: SES gives you DNS records, which Route 53 can add for you) or one email
   address made for Phantom. New SES accounts are in a *sandbox* that only sends to verified addresses, so open
   **SES > Account dashboard > Request production access** (use case: "transactional: one welcome email when
   someone signs up for early access"). Approval usually takes a day.
5. **Update the stack.** CloudFormation > `phantom-website` > **Update** > *Use existing template*, and fill in:
   `AuthDomainPrefix` (step 1), `GoogleClientId` and `GoogleClientSecret` (step 3), `SenderEmail` (the address or
   any address at the domain verified in step 4; leave it empty to skip the email). The outputs now show
   `AuthDomain` and `AuthClientId`.
6. **Fill in `site.json`**: `auth_domain` = `AuthDomain`, `auth_client_id` = `AuthClientId`. Merge to `main`.

Who has signed up: AWS console > **Cognito > User pools > phantom-website-accounts > Users** (name, email, date). To
remove someone (they asked, see the privacy page), select them and **Delete**.

To try sign-in on your laptop, build the site with the real `site.json` and serve it at
`http://localhost:8000` (that address is allowed as well).

## What it costs

For a new site: S3 storage is cents, CloudFront's free tier covers 1 TB and 10 million requests a month, the
certificate is free. Expect under $1 a month, plus $0.50 a month for a Route 53 hosted zone and the domain's yearly
price if you use one. Cognito is free up to 10,000 people a month, SES is $0.10 per 1,000 emails. The budget alarm emails you at 80% of `MonthlyBudgetUsd`.

## How it's built

- **S3** keeps the files: private, encrypted, versioned (old versions are kept 30 days, so a bad publish can be
  rolled back). Only this CloudFront distribution can read it.
- **CloudFront** serves the site from locations around the world, HTTPS only, with security headers (HSTS, a
  strict Content-Security-Policy, no framing). A small CloudFront function sends `www.` to the bare domain.
- **ACM** issues the HTTPS certificate for the domain and renews it; **Route 53** points the domain at CloudFront.
- **IAM**: GitHub's OIDC provider and a role that can only upload to this bucket and refresh this distribution.
- **Cognito** (when `GoogleClientId` is set): the user pool (the early access list), its sign-in domain, Google as
  the only way in, and a public client for the website (authorization code + PKCE, no secret in the browser).
- **Lambda + SES** (when `SenderEmail` is set): a small function that runs on someone's first sign-in and sends
  the confirmation email. If sending fails, the sign-in still goes through.
- To remove everything: delete the stack in CloudFormation. The bucket is kept on purpose (so a delete can't lose
  the site); empty it and delete it by hand if you want it gone.
