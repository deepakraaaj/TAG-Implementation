# Deploying KritiBot: widget on CDN, backend on one EC2

The chatbot widget builds to a single static IIFE bundle (`dist/kritibot-widget.js`
+ SRI hash) whose `backendUrl` is **runtime config**. That makes it ideal for a
CDN: host the JS on S3 + CloudFront, run only the backend on EC2, and update the
widget by re-uploading the file (no backend redeploy).

```
[host page] --<script src=CDN>--> kritibot-widget.js        (S3 + CloudFront)
      │  KritiBot.init({ backendUrl: "https://api.yourdomain.com", appId: "VTSDMS" })
      ▼
  https://api.yourdomain.com  -->  EC2: FastAPI + redis + postgres/MySQL
```

The `chatbot_demo` (nginx) container is only a local demo harness — it is **not**
deployed in this topology. Its `/api/` proxy is replaced by the browser calling
the backend's public URL directly (hence the CORS step below).

---

## 1. Backend CORS (replaces the nginx /api proxy)

CORS origins must be the **sites where the widget is embedded** — the page that
runs `fetch()`. The CloudFront/S3 domain is NOT a CORS origin.

`.env` on the EC2:

```dotenv
APP_ENV=production
# Comma-separated, exact scheme+host(+port), no trailing slash, no "*"
CORS_ORIGINS=https://app.kritilabs.com,https://portal.customer.com
CORS_ALLOW_CREDENTIALS=true
```

Notes:
- In production the backend rejects empty `CORS_ORIGINS` and any `*`
  (see `app/config.py` validators). List every embedding domain explicitly.
- Auth is header-based (`X-User-Context`), so cookies aren't required; you may
  set `CORS_ALLOW_CREDENTIALS=false` if you prefer.
- Add a new customer = append their origin here and restart the backend.

Put the backend behind HTTPS (ALB + ACM cert, or nginx/Caddy on the EC2) so the
widget — served over https from the CDN — can call it without mixed-content.

---

## 2. S3 bucket (private; served only through CloudFront)

```bash
export BUCKET=kritibot-widget-cdn
export REGION=ap-south-1

aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" \
  --create-bucket-configuration LocationConstraint="$REGION"

# Keep it fully private; CloudFront reaches it via Origin Access Control (OAC).
aws s3api put-public-access-block --bucket "$BUCKET" \
  --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
```

Upload the build (content-type + cache header matter):

```bash
npm run build   # in ChatBot-Widget/, outputs dist/kritibot-widget.js
aws s3 cp dist/kritibot-widget.js "s3://$BUCKET/kritibot-widget.js" \
  --content-type "application/javascript" \
  --cache-control "public, max-age=300"   # short TTL; CloudFront caches longer
```

---

## 3. CloudFront distribution (OAC origin + JS cache behavior)

Create an Origin Access Control, then a distribution whose origin is the S3
bucket. Simplest via console; CLI outline:

```bash
# 3a. Origin Access Control (signs requests to the private bucket)
aws cloudfront create-origin-access-control --origin-access-control-config \
  Name=kritibot-oac,SigningProtocol=sigv4,SigningBehavior=always,OriginAccessControlOriginType=s3

# 3b. Create the distribution (use the console or a distribution-config.json):
#   - Origin domain: $BUCKET.s3.$REGION.amazonaws.com
#   - Origin access:  Origin access control settings -> kritibot-oac
#   - Viewer protocol policy: Redirect HTTP to HTTPS
#   - Allowed methods: GET, HEAD
#   - Compress objects automatically: Yes   (gzip/brotli the 276 KB bundle)
#   - Cache policy: CachingOptimized
#   - (Optional) Alternate domain name (CNAME): cdn.yourdomain.com + ACM cert (us-east-1)
```

After the distribution exists, attach the bucket policy that allows only this
distribution to read (replace ACCOUNT_ID + DIST_ID):

```bash
aws s3api put-bucket-policy --bucket "$BUCKET" --policy '{
  "Version": "2012-10-17",
  "Statement": [{
    "Sid": "AllowCloudFrontOAC",
    "Effect": "Allow",
    "Principal": { "Service": "cloudfront.amazonaws.com" },
    "Action": "s3:GetObject",
    "Resource": "arn:aws:s3:::kritibot-widget-cdn/*",
    "Condition": { "StringEquals": {
      "AWS:SourceArn": "arn:aws:cloudfront::ACCOUNT_ID:distribution/DIST_ID"
    }}
  }]
}'
```

The widget is now at `https://<dist>.cloudfront.net/kritibot-widget.js`
(or `https://cdn.yourdomain.com/kritibot-widget.js` with the CNAME).

---

## 4. Embed snippet (host page)

```html
<script src="https://cdn.yourdomain.com/kritibot-widget.js"
        crossorigin="anonymous"></script>
<script>
  KritiBot.init({
    backendUrl: "https://api.yourdomain.com",   // runtime — change without rebuilding
    appId: "VTSDMS"
  });
</script>
```

`integrity="sha384-…"` (from `dist/kritibot-widget.sri.json`) can be added for
Subresource Integrity — but see the cache-busting note: the hash changes every
build, so SRI + a stable filename means updating the hash on every release.

---

## 5. Update loop (your frequent-layout-change case)

```bash
npm run build
aws s3 cp dist/kritibot-widget.js "s3://$BUCKET/kritibot-widget.js" \
  --content-type "application/javascript" --cache-control "public, max-age=300"
aws cloudfront create-invalidation --distribution-id DIST_ID --paths "/kritibot-widget.js"
```

Backend is never touched. Changing `backendUrl` needs no rebuild at all (it's set
in the embed snippet).

### Cache-busting choice
- **Stable filename + invalidation** (above): embedders never change their snippet;
  you invalidate each release. Drop SRI (hash changes each build) for simplicity.
- **Versioned filename** (`kritibot-widget.v3.js`): immutable, `max-age=31536000`,
  no invalidation, SRI-friendly — but embedders must bump the URL each release.
  Best when you don't control the host pages.

---

## What runs where

| Component        | Location                    | Public? |
|------------------|-----------------------------|---------|
| Widget JS bundle | S3 + CloudFront (CDN)       | yes (CDN) |
| Backend (FastAPI)| EC2 (behind HTTPS)          | yes (api.*) |
| Redis            | EC2 (docker, internal)      | no |
| Postgres (VTS)   | EC2 host / container / RDS  | no |
| MySQL (fits/ims) | wherever it lives today     | no |

One EC2, one CDN, zero widget servers.
