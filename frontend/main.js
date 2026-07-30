(function () {
  'use strict';

  const sdk = window.__hpkg_sdk;
  const { html, useState, useEffect, useCallback } = sdk;
  const { SdkDataTable, SdkConfirmModal } = sdk.components;
  const { useToast } = sdk.hooks;

  function StoragePlugin() {
    const { ok, err } = useToast();
    const [activeTab, setActiveTab] = useState('buckets'); // 'buckets', 'keys', 'settings'
    const [buckets, setBuckets] = useState([]);
    const [accessKeys, setAccessKeys] = useState([]);
    const [settings, setSettings] = useState(null);
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
    const [uploadFile, setUploadFile] = useState(null);
    const [uploading, setUploading] = useState(false);

    // Delete Confirmation State
    const [deleteTarget, setDeleteTarget] = useState(null); // { type: 'bucket'|'key'|'object', item: obj }

    // Presign Modal State
    const [presignTarget, setPresignTarget] = useState(null);
    const [presignExpires, setPresignExpires] = useState(3600);
    const [presignResultUrl, setPresignResultUrl] = useState('');

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
        if (sRes) setSettings(sRes);
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

    const handleUploadObject = async (e) => {
      e.preventDefault();
      if (!uploadFile || !selectedBucket) return;
      setUploading(true);
      try {
        const formData = new FormData();
        const objectKey = currentPrefix ? `${currentPrefix}${uploadFile.name}` : uploadFile.name;
        formData.append('key', objectKey);
        formData.append('file', uploadFile);

        const token = localStorage.getItem('token');
        const response = await fetch(`/cpanelapi/storage/buckets/${selectedBucket.name}/objects/upload`, {
          method: 'POST',
          headers: token ? { Authorization: `Bearer ${token}` } : {},
          body: formData,
        });

        if (!response.ok) {
          const errorData = await response.json();
          throw new Error(errorData.detail || 'Upload failed');
        }

        ok(`Uploaded ${uploadFile.name}`);
        setUploadFile(null);
        loadBucketObjects(selectedBucket.name, currentPrefix);
      } catch (e) {
        err(e.message || 'Failed to upload object');
      } finally {
        setUploading(false);
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
    const s3Endpoint = window.location.protocol + '//' + window.location.hostname + ':9000';

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
            <div class="card-title">S3 API Endpoint</div>
            <div style=${{ fontSize: 13, fontFamily: 'var(--font-mono)', color: 'var(--ok)', marginTop: 8, wordBreak: 'break-all' }}>${s3Endpoint}</div>
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
                <span class=${`badge ${selectedBucket.public_access ? 'badge-warn' : 'badge-ok'}`} style=${{ marginLeft: 12 }}>
                  ${selectedBucket.public_access ? 'Public' : 'Private'}
                </span>
                <p class="page-desc" style=${{ marginTop: 4 }}>Path: /data/storage/buckets/${selectedBucket.name}/</p>
              </div>
              <div style=${{ display: 'flex', gap: 8 }}>
                <button class="btn btn-ghost btn-sm" onClick=${() => setSelectedBucket(null)}>Close Browser</button>
              </div>
            </div>

            <!-- Upload Object Bar -->
            <form onSubmit=${handleUploadObject} style=${{ display: 'flex', gap: 12, alignItems: 'center', background: 'var(--bg-3)', padding: 12, borderRadius: 'var(--radius-sm)', marginBottom: 16 }}>
              <input type="file" onChange=${(e) => setUploadFile(e.target.files[0])} style=${{ flex: 1 }} />
              <button type="submit" class="btn btn-primary btn-sm" disabled=${!uploadFile || uploading}>
                ${uploading ? 'Uploading...' : 'Upload File'}
              </button>
            </form>

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
              empty=${{ title: 'No objects found', desc: 'Upload a file or send S3 PUT requests to fill this bucket.' }}
              renderActions=${(row) => html`
                <div style=${{ display: 'flex', gap: 6 }}>
                  ${!row.is_dir && html`
                    <a class="btn btn-ghost btn-sm" href=${`/cpanelapi/storage/buckets/${selectedBucket.name}/objects/download/${encodeURIComponent(row.key)}`} download target="_blank">
                      Download
                    </a>
                    <button class="btn btn-outline btn-sm" onClick=${() => { setPresignTarget(row); setPresignResultUrl(''); }}>Presign</button>
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

        <!-- Tab 3: Admin Settings -->
        ${activeTab === 'settings' && settings && html`
          <div class="card">
            <span class="card-title">Storage & Engine Configuration</span>
            <div style=${{ display: 'flex', flexDirection: 'column', gap: 16, marginTop: 16 }}>
              <div class="field">
                <label>Data Storage Path (Mount Location)</label>
                <input type="text" value=${settings.storage_path} disabled />
                <span class="page-desc">Data location configured on /data mount point: ${settings.storage_path}</span>
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
            </div>
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
            <div class="modal animate-fade-in" style=${{ width: 500 }}>
              <div class="modal-header">
                <span class="modal-title">Generate Presigned URL</span>
                <button class="modal-close" onClick=${() => setPresignTarget(null)}>x</button>
              </div>
              <form onSubmit=${handleGeneratePresignedUrl}>
                <div class="modal-body" style=${{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                  <div class="field">
                    <label>Object Key</label>
                    <input type="text" value=${presignTarget.key} disabled />
                  </div>
                  <div class="field">
                    <label>Expiration Time (Seconds)</label>
                    <input type="number" value=${presignExpires} onInput=${e => setPresignExpires(e.target.value)} required />
                  </div>
                  ${presignResultUrl && html`
                    <div class="field">
                      <label>Generated URL</label>
                      <textarea rows="3" readonly style=${{ fontFamily: 'var(--font-mono)', fontSize: 12 }}>${presignResultUrl}</textarea>
                    </div>
                  `}
                </div>
                <div class="modal-footer">
                  <button type="button" class="btn btn-ghost btn-sm" onClick=${() => setPresignTarget(null)}>Close</button>
                  <button type="submit" class="btn btn-primary btn-sm">Generate URL</button>
                </div>
              </form>
            </div>
          </div>
        `}
      </div>
    `;
  }

  sdk.register('storage', StoragePlugin);
})();
