(function () {
  'use strict';

  const sdk = window.__hpkg_sdk;
  const { html, useState, useEffect, useCallback } = sdk;
  const { SdkDataTable, SdkConfirmModal } = sdk.components;
  const { useToast } = sdk.hooks;

  function formatBytes(bytes, decimals = 2) {
    if (!bytes || bytes === 0) return '0 Bytes';
    const k = 1024;
    const dm = decimals < 0 ? 0 : decimals;
    const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + ' ' + sizes[i];
  }

  function findAuthToken() {
    let token = sdk?.token || (typeof sdk?.getToken === 'function' ? sdk.getToken() : null);

    if (!token) {
      try {
        for (let i = 0; i < localStorage.length; i++) {
          const val = localStorage.getItem(localStorage.key(i));
          if (val && typeof val === 'string' && val.trim().startsWith('eyJ')) {
            token = val.trim();
            break;
          }
        }
      } catch (e) {}
    }

    if (!token) {
      try {
        for (let i = 0; i < sessionStorage.length; i++) {
          const val = sessionStorage.getItem(sessionStorage.key(i));
          if (val && typeof val === 'string' && val.trim().startsWith('eyJ')) {
            token = val.trim();
            break;
          }
        }
      } catch (e) {}
    }

    if (!token) {
      const match = document.cookie.match(/(?:^|;\s*)(?:token|hp_token|jwt|access_token)=([^;]*)/);
      if (match) token = decodeURIComponent(match[1]);
    }

    return token ? token.replace(/^Bearer\s+/i, '') : '';
  }

  function StoragePlugin() {
    const { ok, err } = useToast();
    const [activeTab, setActiveTab] = useState('buckets'); // 'buckets', 'keys', 'guide', 'settings'
    const [guideTool, setGuideTool] = useState('aws_cli'); // 'aws_cli', 'python', 'nodejs', 'laravel', 'rclone'
    const [selectedGuideKey, setSelectedGuideKey] = useState('YOUR_ACCESS_KEY_ID');
    const [buckets, setBuckets] = useState([]);
    const [accessKeys, setAccessKeys] = useState([]);
    const [settings, setSettings] = useState(null);
    const [customDomainInput, setCustomDomainInput] = useState('');
    const [savingSettings, setSavingSettings] = useState(false);
    const [loading, setLoading] = useState(true);

    // Modal states
    const [showCreateBucketModal, setShowCreateBucketModal] = useState(false);
    const [newBucketName, setNewBucketName] = useState('');
    const [newBucketPublic, setNewBucketPublic] = useState(false);
    const [newBucketQuota, setNewBucketQuota] = useState(5120);

    const [showCreateKeyModal, setShowCreateKeyModal] = useState(false);
    const [keyLabel, setKeyLabel] = useState('');
    const [createdSecretInfo, setCreatedSecretInfo] = useState(null);

    // Object Browser State
    const [selectedBucket, setSelectedBucket] = useState(null);
    const [objects, setObjects] = useState([]);
    const [objectsLoading, setObjectsLoading] = useState(false);
    const [currentPrefix, setCurrentPrefix] = useState('');

    // Upload & Drag-and-Drop State
    const [uploadFile, setUploadFile] = useState(null);
    const [uploading, setUploading] = useState(false);
    const [uploadProgress, setUploadProgress] = useState(0);
    const [isDragging, setIsDragging] = useState(false);

    // Delete Confirmation State
    const [deleteTarget, setDeleteTarget] = useState(null);

    // Presign Modal State
    const [presignTarget, setPresignTarget] = useState(null);
    const [presignExpires, setPresignExpires] = useState(3600);
    const [activePresignRecord, setActivePresignRecord] = useState(null);
    const [presignLoading, setPresignLoading] = useState(false);

    const loadData = useCallback(async () => {
      setLoading(true);
      try {
        const [bRes, kRes, sRes] = await Promise.all([
          sdk.fetch('GET', '/cpanelapi/storage/buckets'),
          sdk.fetch('GET', '/cpanelapi/storage/keys'),
          sdk.fetch('GET', '/cpanelapi/storage/settings').catch(() => null),
        ]);
        setBuckets(bRes || []);
        setAccessKeys(kRes || []);
        if (sRes) {
          setSettings(sRes);
          setCustomDomainInput(sRes.s3_domain || '');
        }
      } catch (e) {
        err(e.message || 'Failed to load storage data');
      } finally {
        setLoading(false);
      }
    }, [err]);

    useEffect(() => {
      loadData();
    }, [loadData]);

    const loadBucketObjects = async (bucketName, prefix = '') => {
      setObjectsLoading(true);
      try {
        const query = prefix ? `?prefix=${encodeURIComponent(prefix)}&delimiter=/` : '?delimiter=/';
        const res = await sdk.fetch('GET', `/cpanelapi/storage/buckets/${bucketName}/objects${query}`);
        setObjects(res || []);
        setCurrentPrefix(prefix);
      } catch (e) {
        err(e.message || 'Failed to load objects');
      } finally {
        setObjectsLoading(false);
      }
    };

    const openBucketBrowser = (bucket) => {
      setSelectedBucket(bucket);
      setUploadFile(null);
      setUploadProgress(0);
      loadBucketObjects(bucket.name, '');
    };

    const resetBucketModal = () => {
      setShowCreateBucketModal(false);
      setNewBucketName('');
      setNewBucketPublic(false);
      setNewBucketQuota(5120);
    };

    const handleCreateBucket = async (e) => {
      e.preventDefault();
      try {
        await sdk.fetch('POST', '/cpanelapi/storage/buckets', {
          name: newBucketName,
          public_access: newBucketPublic,
          quota_mb: parseInt(newBucketQuota, 10) || 5120,
        });
        ok(`Bucket '${newBucketName}' created successfully`);
        resetBucketModal();
        loadData();
      } catch (e) {
        err(e.message || 'Failed to create bucket');
        resetBucketModal();
        loadData();
      }
    };

    const handleCreateKey = async (e) => {
      e.preventDefault();
      try {
        const res = await sdk.fetch('POST', '/cpanelapi/storage/keys', {
          label: keyLabel,
        });
        setCreatedSecretInfo(res);
        ok('S3 Access Key generated');
        setKeyLabel('');
        loadData();
      } catch (e) {
        err(e.message || 'Failed to create key');
      }
    };

    const handleSaveSettings = async (e) => {
      e.preventDefault();
      setSavingSettings(true);
      try {
        const updated = await sdk.fetch('POST', '/cpanelapi/storage/settings', {
          s3_domain: customDomainInput.trim(),
        });
        setSettings(updated);
        ok('Storage settings saved successfully');
      } catch (e) {
        err(e.message || 'Failed to save settings');
      } finally {
        setSavingSettings(false);
      }
    };

    const handleOpenPresignModal = async (row) => {
      setPresignTarget(row);
      setActivePresignRecord(null);
      setPresignExpires(3600);
      setPresignLoading(true);
      try {
        const res = await sdk.fetch('GET', `/cpanelapi/storage/buckets/${selectedBucket.name}/objects/presign/active?object_key=${encodeURIComponent(row.key)}`);
        if (res && res.url) {
          setActivePresignRecord(res);
        }
      } catch (e) {
        console.log('No active presign record found');
      } finally {
        setPresignLoading(false);
      }
    };

    const handleGeneratePresignedUrl = async (e) => {
      e.preventDefault();
      if (!presignTarget || !selectedBucket) return;
      try {
        const expIn = parseInt(presignExpires, 10);
        const res = await sdk.fetch('POST', `/cpanelapi/storage/buckets/${selectedBucket.name}/objects/presign`, {
          object_key: presignTarget.key,
          expires_in: isNaN(expIn) ? 3600 : expIn,
        });
        setActivePresignRecord(res);
        ok('Presigned URL generated successfully');
      } catch (e) {
        err(e.message || 'Failed to generate presigned URL');
      }
    };

    const handleRevokePresignedUrl = async () => {
      if (!activePresignRecord || !selectedBucket) return;
      try {
        await sdk.fetch('DELETE', `/cpanelapi/storage/buckets/${selectedBucket.name}/objects/presign/${activePresignRecord.id}`);
        ok('Presigned URL revoked successfully');
        setActivePresignRecord(null);
      } catch (e) {
        err(e.message || 'Failed to revoke presigned URL');
      }
    };

    const handleToggleBucketAccess = async (targetBucket) => {
      if (!targetBucket) return;
      const isCurrentlyPublic = typeof targetBucket.public_access === 'boolean' ? targetBucket.public_access : (targetBucket.public_access === 'Public');
      const nextAccess = !isCurrentlyPublic;
      try {
        const updated = await sdk.fetch('PUT', `/cpanelapi/storage/buckets/${targetBucket.name}`, {
          public_access: nextAccess
        });
        ok(`Bucket '${targetBucket.name}' access set to ${nextAccess ? 'Public' : 'Private'}`);
        if (selectedBucket && selectedBucket.name === targetBucket.name) {
          setSelectedBucket(updated);
        }
        loadData();
      } catch (e) {
        err(e.message || 'Failed to update bucket access');
      }
    };

    const handleUploadObject = async (fileToUpload) => {
      const targetFile = fileToUpload || uploadFile;
      if (!targetFile || !selectedBucket) return;

      setUploading(true);
      setUploadProgress(0);

      const formData = new FormData();
      const objectKey = currentPrefix ? `${currentPrefix}${targetFile.name}` : targetFile.name;
      formData.append('key', objectKey);
      formData.append('file', targetFile);

      const token = findAuthToken();
      const queryParam = token ? `?token=${encodeURIComponent(token)}` : '';
      const uploadUrl = `/cpanelapi/storage/buckets/${selectedBucket.name}/objects/upload${queryParam}`;

      // Primary upload path: XMLHttpRequest for real-time progress bar
      const xhr = new XMLHttpRequest();
      xhr.open('POST', uploadUrl);
      xhr.withCredentials = true;

      if (token) {
        xhr.setRequestHeader('Authorization', `Bearer ${token}`);
      }

      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable) {
          const percent = Math.round((event.loaded / event.total) * 100);
          setUploadProgress(percent);
        }
      };

      xhr.onload = async () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          setUploading(false);
          ok(`Uploaded ${targetFile.name}`);
          setUploadFile(null);
          setUploadProgress(0);
          loadBucketObjects(selectedBucket.name, currentPrefix);
        } else if (xhr.status === 401) {
          // Fallback path: use sdk.fetch directly if XHR receives 401
          try {
            await sdk.fetch('POST', uploadUrl, formData);
            ok(`Uploaded ${targetFile.name}`);
            setUploadFile(null);
            setUploadProgress(0);
            loadBucketObjects(selectedBucket.name, currentPrefix);
          } catch (fetchErr) {
            err(fetchErr.message || 'Upload failed (401 Unauthorized)');
          } finally {
            setUploading(false);
          }
        } else {
          setUploading(false);
          try {
            const errJson = JSON.parse(xhr.responseText);
            err(errJson.detail || 'Upload failed');
          } catch (ex) {
            err(`Upload failed with status ${xhr.status}`);
          }
        }
      };

      xhr.onerror = async () => {
        // Fallback to sdk.fetch on network error
        try {
          await sdk.fetch('POST', uploadUrl, formData);
          ok(`Uploaded ${targetFile.name}`);
          setUploadFile(null);
          setUploadProgress(0);
          loadBucketObjects(selectedBucket.name, currentPrefix);
        } catch (fetchErr) {
          err(fetchErr.message || 'Network error during upload');
        } finally {
          setUploading(false);
        }
      };

      xhr.send(formData);
    };

    const handleDragOver = (e) => {
      e.preventDefault();
      setIsDragging(true);
    };

    const handleDragLeave = (e) => {
      e.preventDefault();
      setIsDragging(false);
    };

    const handleDrop = (e) => {
      e.preventDefault();
      setIsDragging(false);
      if (e.dataTransfer.files && e.dataTransfer.files[0]) {
        const droppedFile = e.dataTransfer.files[0];
        setUploadFile(droppedFile);
      }
    };

    const handleGeneratePresignedUrl = async (e) => {
      e.preventDefault();
      if (!presignTarget || !selectedBucket) return;
      try {
        const res = await sdk.fetch('POST', `/cpanelapi/storage/buckets/${selectedBucket.name}/objects/presign`, {
          object_key: presignTarget.key,
          expires_in: parseInt(presignExpires, 10) || 3600,
        });
        setPresignResultUrl(res.url);
        ok('Presigned URL generated');
      } catch (e) {
        err(e.message || 'Failed to generate presigned URL');
      }
    };

    const confirmDelete = async () => {
      if (!deleteTarget) return;
      try {
        if (deleteTarget.type === 'bucket') {
          await sdk.fetch('DELETE', `/cpanelapi/storage/buckets/${deleteTarget.item.name}?force=true`);
          ok(`Bucket '${deleteTarget.item.name}' deleted`);
          if (selectedBucket?.name === deleteTarget.item.name) setSelectedBucket(null);
        } else if (deleteTarget.type === 'key') {
          await sdk.fetch('DELETE', `/cpanelapi/storage/keys/${deleteTarget.item.access_key}`);
          ok(`Key '${deleteTarget.item.access_key}' deleted`);
        } else if (deleteTarget.type === 'object') {
          await sdk.fetch('DELETE', `/cpanelapi/storage/buckets/${selectedBucket.name}/objects/${encodeURIComponent(deleteTarget.item.key)}`);
          ok(`Object '${deleteTarget.item.key}' deleted`);
          loadBucketObjects(selectedBucket.name, currentPrefix);
        }
        setDeleteTarget(null);
        loadData();
      } catch (e) {
        err(e.message || 'Delete operation failed');
      }
    };

    const totalUsedBytes = buckets.reduce((acc, b) => acc + (b.used_bytes || 0), 0);
    const totalUsedMb = (totalUsedBytes / (1024 * 1024)).toFixed(2);

    const directHttpEndpoint = 'http://0.0.0.0:9000';
    const activeDomain = settings?.s3_domain ? settings.s3_domain.trim() : '';
    const publicS3Endpoint = activeDomain
      ? (activeDomain.startsWith('http://') || activeDomain.startsWith('https://') ? activeDomain : `https://${activeDomain}`)
      : directHttpEndpoint;

    const renderGuideContent = () => {
      const sampleKey = selectedGuideKey || 'YOUR_ACCESS_KEY_ID';

      if (guideTool === 'aws_cli') {
        return `# Configure AWS CLI credentials
aws configure set aws_access_key_id ${sampleKey}
aws configure set aws_secret_access_key YOUR_SECRET_ACCESS_KEY
aws configure set default.region us-east-1

# List Buckets (S3 Endpoint)
aws s3 ls --endpoint-url ${publicS3Endpoint}

# Upload File to Bucket
aws s3 cp myfile.txt s3://my-bucket/ --endpoint-url ${publicS3Endpoint}`;
      }
      if (guideTool === 'python') {
        return `import boto3

# Initialize S3 Client
s3 = boto3.client(
    's3',
    endpoint_url='${publicS3Endpoint}',
    aws_access_key_id='${sampleKey}',
    aws_secret_access_key='YOUR_SECRET_ACCESS_KEY',
    region_name='us-east-1'
)

# Upload File
s3.upload_file('local_photo.jpg', 'my-bucket', 'uploads/photo.jpg')

# Download File
s3.download_file('my-bucket', 'uploads/photo.jpg', 'downloaded_photo.jpg')

# List Objects in Bucket
response = s3.list_objects_v2(Bucket='my-bucket')
for obj in response.get('Contents', []):
    print(obj['Key'], obj['Size'])`;
      }
      if (guideTool === 'nodejs') {
        return `import { S3Client, PutObjectCommand, ListObjectsV2Command } from "@aws-sdk/client-s3";
import { readFileSync } from "fs";

const s3 = new S3Client({
  endpoint: "${publicS3Endpoint}",
  region: "us-east-1",
  credentials: {
    accessKeyId: "${sampleKey}",
    secretAccessKey: "YOUR_SECRET_ACCESS_KEY",
  },
  forcePathStyle: true,
});

// Upload Object
await s3.send(new PutObjectCommand({
  Bucket: "my-bucket",
  Key: "hello.txt",
  Body: readFileSync("./hello.txt"),
}));

// List Objects
const data = await s3.send(new ListObjectsV2Command({ Bucket: "my-bucket" }));
console.log(data.Contents);`;
      }
      if (guideTool === 'laravel') {
        return `// Add to config/filesystems.php in Laravel / PHP app:

'disks' => [
    'hostpanel_s3' => [
        'driver' => 's3',
        'key' => env('AWS_ACCESS_KEY_ID', '${sampleKey}'),
        'secret' => env('AWS_SECRET_ACCESS_KEY', 'YOUR_SECRET_ACCESS_KEY'),
        'region' => 'us-east-1',
        'bucket' => env('AWS_BUCKET', 'my-bucket'),
        'endpoint' => '${publicS3Endpoint}',
        'use_path_style_endpoint' => true,
    ],
],

// Laravel Usage:
Storage::disk('hostpanel_s3')->put('avatar.png', $fileContents);`;
      }
      if (guideTool === 'rclone') {
        return `# Add to ~/.config/rclone/rclone.conf:

[hostpanel-s3]
type = s3
provider = Minio
env_auth = false
access_key_id = ${sampleKey}
secret_access_key = YOUR_SECRET_ACCESS_KEY
endpoint = ${publicS3Endpoint}

# Rclone commands:
rclone ls hostpanel-s3:my-bucket
rclone sync ./my-folder hostpanel-s3:my-bucket/backup`;
      }
      return '';
    };

    return html`
      <div class="page">
        <!-- Page Header -->
        <div class="page-header">
          <div>
            <h1 class="page-title">Object Storage (S3)</h1>
            <p class="page-desc">AWS S3-compatible bucket & object storage management service</p>
          </div>
          <div style=${{ display: 'flex', gap: 8 }}>
            <button class="btn btn-outline btn-sm" onClick=${loadData}>Refresh</button>
            <button class="btn btn-primary btn-sm" onClick=${() => { resetBucketModal(); setShowCreateBucketModal(true); }}>+ Create Bucket</button>
            <button class="btn btn-ghost btn-sm" onClick=${() => setShowCreateKeyModal(true)}>+ New Access Key</button>
          </div>
        </div>

        <!-- Metrics Overview Row -->
        <div style=${{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 16, marginBottom: 20 }}>
          <div class="card" style=${{ padding: 16 }}>
            <div class="card-title">Total Buckets</div>
            <div style=${{ fontSize: 24, fontWeight: 'bold', color: 'var(--text)', marginTop: 4 }}>${buckets.length}</div>
          </div>

          <div class="card" style=${{ padding: 16 }}>
            <div class="card-title">Storage Used</div>
            <div style=${{ fontSize: 24, fontWeight: 'bold', color: 'var(--accent)', marginTop: 4 }}>${totalUsedMb} MB</div>
          </div>

          <div class="card" style=${{ padding: 16 }}>
            <div class="card-title">Access Keys</div>
            <div style=${{ fontSize: 24, fontWeight: 'bold', color: 'var(--text)', marginTop: 4 }}>${accessKeys.length}</div>
          </div>

          <div class="card" style=${{ padding: 16 }}>
            <div class="card-title">S3 Engine Direct Listen</div>
            <div style=${{ fontSize: 13, fontFamily: 'var(--font-mono)', color: 'var(--ok)', marginTop: 6, wordBreak: 'break-all' }}>0.0.0.0:9000</div>
            ${activeDomain && html`
              <div style=${{ fontSize: 12, color: 'var(--accent)', marginTop: 4, wordBreak: 'break-all' }}>Proxy Domain: ${publicS3Endpoint}</div>
            `}
          </div>
        </div>

        <!-- Tabs Header -->
        <div style=${{ display: 'flex', gap: 12, borderBottom: '1px solid var(--border)', marginBottom: 20 }}>
          <button
            class=${`btn ${activeTab === 'buckets' ? 'btn-primary' : 'btn-ghost'} btn-sm`}
            style=${{ borderRadius: '4px 4px 0 0' }}
            onClick=${() => { setActiveTab('buckets'); setSelectedBucket(null); }}
          >
            Buckets (${buckets.length})
          </button>
          <button
            class=${`btn ${activeTab === 'keys' ? 'btn-primary' : 'btn-ghost'} btn-sm`}
            style=${{ borderRadius: '4px 4px 0 0' }}
            onClick=${() => setActiveTab('keys')}
          >
            Access Keys (${accessKeys.length})
          </button>
          <button
            class=${`btn ${activeTab === 'guide' ? 'btn-primary' : 'btn-ghost'} btn-sm`}
            style=${{ borderRadius: '4px 4px 0 0' }}
            onClick=${() => setActiveTab('guide')}
          >
            Integration Guide
          </button>
          ${settings && html`
            <button
              class=${`btn ${activeTab === 'settings' ? 'btn-primary' : 'btn-ghost'} btn-sm`}
              style=${{ borderRadius: '4px 4px 0 0' }}
              onClick=${() => setActiveTab('settings')}
            >
              Storage Settings
            </button>
          `}
        </div>

        <!-- Selected Bucket Object Browser View -->
        ${selectedBucket && html`
          <div class="card" style=${{ marginBottom: 20 }}>
            <div style=${{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
              <div>
                <span class="card-title" style=${{ marginBottom: 0 }}>Bucket: ${selectedBucket.name}</span>
                <span class=${`badge ${selectedBucket.public_access ? 'badge-warn' : 'badge-ok'}`} style=${{ marginLeft: 12, cursor: 'pointer' }} onClick=${() => handleToggleBucketAccess(selectedBucket)} title="Click to toggle Public / Private access">
                  ${selectedBucket.public_access ? 'Public' : 'Private'}
                </span>
                <p class="page-desc" style=${{ marginTop: 4 }}>Path: /data/storage/buckets/${selectedBucket.name}/</p>
              </div>
              <div style=${{ display: 'flex', gap: 8 }}>
                <button class="btn btn-outline btn-sm" onClick=${() => handleToggleBucketAccess(selectedBucket)}>
                  Make ${selectedBucket.public_access ? 'Private' : 'Public'}
                </button>
                <button class="btn btn-ghost btn-sm" onClick=${() => setSelectedBucket(null)}>Close Browser</button>
              </div>
            </div>

            <!-- Modern Drag & Drop Upload Zone -->
            <div
              style=${{
                border: `2px dashed ${isDragging ? 'var(--accent)' : 'var(--border)'}`,
                borderRadius: 'var(--radius-md, 8px)',
                padding: '24px 20px',
                textAlign: 'center',
                background: isDragging ? 'var(--bg-hover)' : 'var(--bg-3)',
                transition: 'all 0.2s ease',
                marginBottom: 20,
                position: 'relative'
              }}
              onDragOver=${handleDragOver}
              onDragLeave=${handleDragLeave}
              onDrop=${handleDrop}
            >
              ${!uploadFile ? html`
                <div style=${{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8 }}>
                  <div style=${{ fontSize: 28, color: 'var(--accent)' }}>☁️</div>
                  <div style=${{ fontSize: 14, fontWeight: 500, color: 'var(--text)' }}>
                    Drag & drop file here, or <label style=${{ color: 'var(--accent)', cursor: 'pointer', textDecoration: 'underline', margin: 0 }}>browse<input type="file" style=${{ display: 'none' }} onChange=${(e) => e.target.files && e.target.files[0] && setUploadFile(e.target.files[0])} /></label>
                  </div>
                  <span class="page-desc">Upload files up to bucket quota capacity (${selectedBucket.quota_mb} MB)</span>
                </div>
              ` : html`
                <div style=${{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 12 }}>
                  <div style=${{ display: 'flex', alignItems: 'center', gap: 12, background: 'var(--bg-card)', padding: '10px 16px', borderRadius: 'var(--radius-sm)', border: '1px solid var(--border)', maxWidth: '100%', width: '400px' }}>
                    <span style=${{ fontSize: 24 }}>📄</span>
                    <div style=${{ flex: 1, textAlign: 'left', overflow: 'hidden' }}>
                      <div style=${{ fontSize: 13, fontWeight: 600, textOverflow: 'ellipsis', overflow: 'hidden', whiteSpace: 'nowrap' }}>${uploadFile.name}</div>
                      <div style=${{ fontSize: 11, color: 'var(--text-muted)' }}>${formatBytes(uploadFile.size)}</div>
                    </div>
                    ${!uploading && html`
                      <button type="button" class="btn btn-ghost btn-sm" style=${{ padding: '2px 8px' }} onClick=${() => { setUploadFile(null); setUploadProgress(0); }}>✕</button>
                    `}
                  </div>

                  <!-- Progress Bar -->
                  ${uploading && html`
                    <div style=${{ width: '100%', maxWidth: '400px' }}>
                      <div style=${{ height: '8px', background: 'var(--bg-hover)', borderRadius: '4px', overflow: 'hidden' }}>
                        <div style=${{ width: `${uploadProgress}%`, height: '100%', background: 'var(--accent)', transition: 'width 0.15s ease' }}></div>
                      </div>
                      <div style=${{ fontSize: 12, marginTop: 4, fontWeight: 600, color: 'var(--accent)' }}>
                        ${uploadProgress}% Uploading...
                      </div>
                    </div>
                  `}

                  <div style=${{ display: 'flex', gap: 8, marginTop: 4 }}>
                    <button
                      type="button"
                      class="btn btn-primary btn-sm"
                      disabled=${uploading}
                      onClick=${() => handleUploadObject(uploadFile)}
                    >
                      ${uploading ? `Uploading (${uploadProgress}%)` : 'Start Upload'}
                    </button>
                    ${!uploading && html`
                      <button type="button" class="btn btn-ghost btn-sm" onClick=${() => setUploadFile(null)}>Cancel</button>
                    `}
                  </div>
                </div>
              `}
            </div>

            <!-- Objects List Table -->
            <${SdkDataTable}
              columns=${[
                { key: 'key', label: 'Object Key', type: 'mono' },
                { key: 'size_formatted', label: 'Size' },
                { key: 'content_type', label: 'Type' },
                { key: 'last_modified', label: 'Last Modified' },
              ]}
              rows=${objects}
              loading=${objectsLoading}
              empty=${{ title: 'No objects found', desc: 'Upload a file using the dropzone above or send S3 PUT requests to fill this bucket.' }}
              renderActions=${(row) => html`
                <div style=${{ display: 'flex', gap: 6 }}>
                  ${!row.is_dir && html`
                    <a class="btn btn-ghost btn-sm" href=${`/cpanelapi/storage/buckets/${selectedBucket.name}/objects/download/${encodeURIComponent(row.key)}?token=${encodeURIComponent(findAuthToken())}`} download target="_blank">
                      Download
                    </a>
                    <button class="btn btn-outline btn-sm" onClick=${() => handleOpenPresignModal(row)}>Presign</button>
                  `}
                  <button class="btn btn-danger btn-sm" onClick=${() => setDeleteTarget({ type: 'object', item: row })}>Delete</button>
                </div>
              `}
            />
          </div>
        `}

        <!-- Tab 1: Buckets List -->
        ${activeTab === 'buckets' && !selectedBucket && html`
          <div class="card">
            <div style=${{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
              <span class="card-title" style=${{ marginBottom: 0 }}>Active Storage Buckets</span>
            </div>

            <${SdkDataTable}
              columns=${[
                { key: 'name', label: 'Bucket Name', type: 'mono' },
                { key: 'owner', label: 'Owner' },
                { key: 'public_access', label: 'Access', type: 'badge' },
                { key: 'used_mb', label: 'Used (MB)' },
                { key: 'quota_mb', label: 'Quota (MB)' },
                { key: 'object_count', label: 'Objects' },
              ]}
              rows=${buckets.map(b => ({
                ...b,
                public_access: b.public_access ? 'Public' : 'Private',
              }))}
              loading=${loading}
              empty=${{ title: 'No storage buckets', desc: 'Click "Create Bucket" above to create your first S3 bucket.' }}
              renderActions=${(row) => html`
                <div style=${{ display: 'flex', gap: 6 }}>
                  <button class="btn btn-primary btn-sm" onClick=${() => openBucketBrowser(row)}>Browse</button>
                  <button class="btn btn-outline btn-sm" onClick=${() => handleToggleBucketAccess(row)}>
                    Make ${row.public_access === 'Public' ? 'Private' : 'Public'}
                  </button>
                  <button class="btn btn-danger btn-sm" onClick=${() => setDeleteTarget({ type: 'bucket', item: row })}>Delete</button>
                </div>
              `}
            />
          </div>
        `}

        <!-- Tab 2: Access Keys List -->
        ${activeTab === 'keys' && html`
          <div class="card">
            <div style=${{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
              <span class="card-title" style=${{ marginBottom: 0 }}>S3 Credentials (Access Key IDs)</span>
            </div>

            <${SdkDataTable}
              columns=${[
                { key: 'access_key', label: 'Access Key ID', type: 'mono' },
                { key: 'label', label: 'Label' },
                { key: 'owner', label: 'Owner' },
                { key: 'status', label: 'Status', type: 'badge' },
                { key: 'created_at', label: 'Created' },
              ]}
              rows=${accessKeys}
              loading=${loading}
              empty=${{ title: 'No access keys', desc: 'Click "New Access Key" above to generate AWS-compatible credentials.' }}
              renderActions=${(row) => html`
                <button class="btn btn-danger btn-sm" onClick=${() => setDeleteTarget({ type: 'key', item: row })}>Revoke</button>
              `}
            />
          </div>
        `}

        <!-- Tab 3: Integration Guide -->
        ${activeTab === 'guide' && html`
          <div class="card">
            <div style=${{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
              <div>
                <span class="card-title" style=${{ marginBottom: 0 }}>SDK & Tool Integration Guide</span>
                <p class="page-desc" style=${{ marginTop: 4 }}>Connect your applications, scripts, or backup tools to HostPanel S3 Object Storage</p>
              </div>
              <div style=${{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <label style=${{ marginBottom: 0, fontSize: 13 }}>Use Access Key:</label>
                <select
                  class="field"
                  style=${{ padding: '4px 8px', fontSize: 13, minWidth: 200 }}
                  value=${selectedGuideKey}
                  onChange=${(e) => setSelectedGuideKey(e.target.value)}
                >
                  <option value="YOUR_ACCESS_KEY_ID">YOUR_ACCESS_KEY_ID (Placeholder)</option>
                  ${accessKeys.map(k => html`<option value=${k.access_key}>${k.access_key} (${k.label || 'No label'})</option>`)}
                </select>
              </div>
            </div>

            <!-- Sub-tab Selector for Languages/SDKs -->
            <div style=${{ display: 'flex', gap: 8, marginBottom: 16, flexWrap: 'wrap' }}>
              <button class=${`btn ${guideTool === 'aws_cli' ? 'btn-primary' : 'btn-outline'} btn-sm`} onClick=${() => setGuideTool('aws_cli')}>AWS CLI</button>
              <button class=${`btn ${guideTool === 'python' ? 'btn-primary' : 'btn-outline'} btn-sm`} onClick=${() => setGuideTool('python')}>Python (boto3)</button>
              <button class=${`btn ${guideTool === 'nodejs' ? 'btn-primary' : 'btn-outline'} btn-sm`} onClick=${() => setGuideTool('nodejs')}>Node.js (AWS SDK v3)</button>
              <button class=${`btn ${guideTool === 'laravel' ? 'btn-primary' : 'btn-outline'} btn-sm`} onClick=${() => setGuideTool('laravel')}>PHP / Laravel</button>
              <button class=${`btn ${guideTool === 'rclone' ? 'btn-primary' : 'btn-outline'} btn-sm`} onClick=${() => setGuideTool('rclone')}>Rclone</button>
            </div>

            <!-- Code Snippet Display with direct value binding -->
            <div class="field">
              <textarea
                rows="15"
                readonly
                value=${renderGuideContent()}
                style=${{ fontFamily: 'var(--font-mono)', fontSize: 13, background: 'var(--bg-3)', color: 'var(--text)', padding: 12, borderRadius: 'var(--radius-sm)' }}
              />
            </div>
          </div>
        `}

        <!-- Tab 4: Admin Settings -->
        ${activeTab === 'settings' && settings && html`
          <div class="card">
            <span class="card-title">Storage & Engine Configuration</span>
            <form onSubmit=${handleSaveSettings} style=${{ display: 'flex', flexDirection: 'column', gap: 16, marginTop: 16 }}>
              <div class="field">
                <label>Data Storage Path (Mount Location)</label>
                <input type="text" value=${settings.storage_path} disabled />
                <span class="page-desc">Data location configured on /data mount point: ${settings.storage_path}</span>
              </div>

              <div class="field">
                <label>Custom S3 Reverse Proxy Domain (e.g. s3.consoleapi.in)</label>
                <input
                  type="text"
                  placeholder="s3.yourdomain.com"
                  value=${customDomainInput}
                  onInput=${(e) => setCustomDomainInput(e.target.value)}
                />
                <span class="page-desc">When configured, code snippets and client SDKs will use your Nginx SSL reverse proxy domain pointing to 0.0.0.0:9000.</span>
              </div>

              <div style=${{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 16 }}>
                <div class="field">
                  <label>Partition Total</label>
                  <input type="text" value=${`${settings.disk_total_mb} MB`} disabled />
                </div>
                <div class="field">
                  <label>Partition Used</label>
                  <input type="text" value=${`${settings.disk_used_mb} MB`} disabled />
                </div>
                <div class="field">
                  <label>Partition Free</label>
                  <input type="text" value=${`${settings.disk_free_mb} MB`} disabled />
                </div>
              </div>

              <div style=${{ display: 'flex', justifyContent: 'flex-end', marginTop: 8 }}>
                <button type="submit" class="btn btn-primary btn-sm" disabled=${savingSettings}>
                  ${savingSettings ? 'Saving...' : 'Save Settings'}
                </button>
              </div>
            </form>
          </div>
        `}

        <!-- Create Bucket Modal -->
        ${showCreateBucketModal && html`
          <div class="modal-overlay" onClick=${e => e.target === e.currentTarget && resetBucketModal()}>
            <div class="modal animate-fade-in" style=${{ width: 440 }}>
              <div class="modal-header">
                <span class="modal-title">Create S3 Bucket</span>
                <button class="modal-close" onClick=${() => resetBucketModal()}>x</button>
              </div>
              <form onSubmit=${handleCreateBucket}>
                <div class="modal-body" style=${{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                  <div class="field">
                    <label>Bucket Name</label>
                    <input type="text" placeholder="my-app-storage" value=${newBucketName} onInput=${e => setNewBucketName(e.target.value)} required />
                    <span class="page-desc">Must be unique, 3-63 lowercase alphanumeric chars or hyphens</span>
                  </div>

                  <div class="field">
                    <label>Storage Quota (MB)</label>
                    <input type="number" value=${newBucketQuota} onInput=${e => setNewBucketQuota(e.target.value)} required />
                  </div>

                  <div class="field" style=${{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                    <input type="checkbox" id="publicAccessChk" checked=${newBucketPublic} onChange=${e => setNewBucketPublic(e.target.checked)} />
                    <label for="publicAccessChk" style=${{ marginBottom: 0 }}>Enable Public Read Access</label>
                  </div>
                </div>
                <div class="modal-footer">
                  <button type="button" class="btn btn-ghost btn-sm" onClick=${() => resetBucketModal()}>Cancel</button>
                  <button type="submit" class="btn btn-primary btn-sm">Create Bucket</button>
                </div>
              </form>
            </div>
          </div>
        `}

        <!-- Create Access Key Modal & One-time Secret View -->
        ${showCreateKeyModal && html`
          <div class="modal-overlay" onClick=${e => e.target === e.currentTarget && setShowCreateKeyModal(false)}>
            <div class="modal animate-fade-in" style=${{ width: 480 }}>
              <div class="modal-header">
                <span class="modal-title">Generate S3 Access Key</span>
                <button class="modal-close" onClick=${() => { setShowCreateKeyModal(false); setCreatedSecretInfo(null); }}>x</button>
              </div>
              ${!createdSecretInfo ? html`
                <form onSubmit=${handleCreateKey}>
                  <div class="modal-body" style=${{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                    <div class="field">
                      <label>Credential Label</label>
                      <input type="text" placeholder="e.g. Backup Script Key" value=${keyLabel} onInput=${e => setKeyLabel(e.target.value)} required />
                    </div>
                  </div>
                  <div class="modal-footer">
                    <button type="button" class="btn btn-ghost btn-sm" onClick=${() => setShowCreateKeyModal(false)}>Cancel</button>
                    <button type="submit" class="btn btn-primary btn-sm">Generate Key</button>
                  </div>
                </form>
              ` : html`
                <div class="modal-body" style=${{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                  <div class="field">
                    <label>Access Key ID</label>
                    <input type="text" value=${createdSecretInfo.access_key} readonly style=${{ fontFamily: 'var(--font-mono)' }} />
                  </div>
                  <div class="field">
                    <label>Secret Access Key (Save now — shown only once!)</label>
                    <input type="text" value=${createdSecretInfo.secret_key} readonly style=${{ fontFamily: 'var(--font-mono)', color: 'var(--ok)' }} />
                  </div>
                </div>
                <div class="modal-footer">
                  <button type="button" class="btn btn-primary btn-sm" onClick=${() => { setShowCreateKeyModal(false); setCreatedSecretInfo(null); }}>Done</button>
                </div>
              `}
            </div>
          </div>
        `}

        <!-- Delete Confirmation Modal -->
        ${deleteTarget && html`
          <${SdkConfirmModal}
            open=${true}
            title=${`Delete ${deleteTarget.type.toUpperCase()}`}
            message=${`Are you sure you want to delete ${deleteTarget.item.name || deleteTarget.item.access_key || deleteTarget.item.key}?`}
            danger=${true}
            onClose=${() => setDeleteTarget(null)}
            onConfirm=${confirmDelete}
          />
        `}

        <!-- Presign Modal -->
        ${presignTarget && html`
          <div class="modal-overlay" onClick=${e => e.target === e.currentTarget && setPresignTarget(null)}>
            <div class="modal animate-fade-in" style=${{ width: 540 }}>
              <div class="modal-header">
                <span class="modal-title">Presigned Public URL</span>
                <button class="modal-close" onClick=${() => setPresignTarget(null)}>x</button>
              </div>
              <div class="modal-body" style=${{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                <div class="field">
                  <label>Object Key</label>
                  <input type="text" value=${presignTarget.key} disabled style=${{ fontFamily: 'var(--font-mono)' }} />
                </div>

                ${presignLoading ? html`
                  <div style=${{ padding: '20px', textAlign: 'center', color: 'var(--text-3)' }}>Checking active presigned URLs...</div>
                ` : activePresignRecord ? html`
                  <div style=${{ display: 'flex', flexDirection: 'column', gap: 12, padding: 14, background: '#0a0b0e', border: '1px solid var(--green-border)', borderRadius: 10 }}>
                    <div style=${{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                      <span class="chip chip-green" style=${{ fontSize: 10 }}>Active Presigned URL</span>
                      <span style=${{ fontSize: 11, color: 'var(--text-3)' }}>
                        ${activePresignRecord.is_never ? 'Never Expires (Unlimited)' : `Expires: ${new Date(activePresignRecord.expires_at * 1000).toLocaleString()}`}
                      </span>
                    </div>
                    <div class="field">
                      <label style=${{ fontSize: 11, color: 'var(--text-3)' }}>Public URL</label>
                      <textarea rows="3" readonly style=${{ fontFamily: 'var(--font-mono)', fontSize: 11.5, background: '#040507', color: 'var(--green)' }}>${activePresignRecord.url}</textarea>
                    </div>
                    <div style=${{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
                      <button type="button" class="btn btn-ghost btn-sm" onClick=${() => { navigator.clipboard.writeText(activePresignRecord.url); ok('URL copied to clipboard'); }}>
                        Copy Link
                      </button>
                      <button type="button" class="btn btn-danger btn-sm" onClick=${handleRevokePresignedUrl}>
                        Revoke / Delete Link
                      </button>
                    </div>
                  </div>
                ` : html`
                  <form onSubmit=${handleGeneratePresignedUrl} style=${{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                    <div class="field">
                      <label>Expiration Duration</label>
                      <select value=${presignExpires} onChange=${e => setPresignExpires(e.target.value)} class="select" style=${{ width: '100%' }}>
                        <option value="3600">1 Hour (3600s)</option>
                        <option value="86400">1 Day (86400s)</option>
                        <option value="604800">7 Days (604800s)</option>
                        <option value="2592000">30 Days (2592000s)</option>
                        <option value="0">Never Expire (Unlimited)</option>
                      </select>
                    </div>
                    <div style=${{ fontSize: 11.5, color: 'var(--text-3)' }}>
                      Generating a presigned URL creates a public link. Only 1 active presigned URL is allowed per object. You can revoke it anytime.
                    </div>
                    <div class="modal-footer" style=${{ padding: '12px 0 0', borderTop: 'none' }}>
                      <button type="button" class="btn btn-ghost btn-sm" onClick=${() => setPresignTarget(null)}>Cancel</button>
                      <button type="submit" class="btn btn-primary btn-sm">Generate URL</button>
                    </div>
                  </form>
                `}
              </div>
            </div>
          </div>
        `}
      </div>
    `;
  }

  sdk.register('storage', StoragePlugin);
})();
