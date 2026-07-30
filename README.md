# HostPanel Package Storage (`hostpanel-package-storage`)

AWS S3-compatible Object Storage plugin package for HostPanel control panel.

## Features
- **S3 Bucket Management**: Create, configure, quota-limit, and delete S3 buckets.
- **AWS Signature V4 Protocol**: Native REST API compatibility with AWS CLI, `boto3`, `@aws-sdk/client-s3`, Cyberduck, and Rclone.
- **Access Key Credentials**: Generate and revoke Access Key IDs & Secret Access Keys.
- **Object Browser & Presigned Links**: Interactive UI file manager, file streaming, and time-limited presigned URLs.
- **Storage Data Path Configuration**: Default object storage location at `/data/storage/` on your `/data` mount point, while keeping the plugin runtime cleanly isolated under `/opt/hostpanel/plugins/storage/`.

## Architecture & Paths
- **Plugin Runtime**: `/opt/hostpanel/plugins/storage/`
- **Frontend UI**: `/opt/hostpanel/frontend/packages/storage/main.js`
- **Default Object Data Directory**: `/data/storage/`
- **S3 API Port**: `9000` (`http://cpanel.consoleapi.in:9000` or `/cpanelapi/storage/s3/`)

## Installation
Build zip package:
```bash
./build.sh
```
Upload `hostpanel-storage-1.0.0.zip` via HostPanel Package Manager UI at `http://cpanel.consoleapi.in:2082`.
