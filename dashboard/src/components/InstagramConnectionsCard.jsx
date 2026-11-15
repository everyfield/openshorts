import React, { useCallback, useEffect, useState } from 'react';
import { Instagram, Loader2, Link2, Unlink } from 'lucide-react';
import { apiJson } from '../lib/api';

function connectionHealthLabel(health) {
  if (health === 'connected') return 'Connected';
  if (health === 'refresh_degraded') return 'Token refresh retry scheduled';
  if (health === 'reauth_required') return 'Reconnect required';
  return health || 'Unknown';
}

function isPublishableHealth(health) {
  return health === 'connected' || health === 'refresh_degraded';
}

export default function InstagramConnectionsCard() {
  const [connections, setConnections] = useState([]);
  const [clips, setClips] = useState([]);
  const [history, setHistory] = useState([]);
  const [selectedConnection, setSelectedConnection] = useState('');
  const [selectedClip, setSelectedClip] = useState('');
  const [caption, setCaption] = useState('');
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');

  const load = useCallback(async () => {
    try {
      const data = await apiJson('/api/native-social/instagram/connections');
      const accounts = Array.isArray(data.connections) ? data.connections : [];
      setConnections(accounts);
      try {
        const historyData = await apiJson('/api/native-social/instagram/publishes?limit=50');
        setHistory(Array.isArray(historyData.publishes) ? historyData.publishes : []);
      } catch (_) {
        setHistory([]);
      }
      setSelectedConnection((current) =>
        accounts.some((x) => x.id === current && isPublishableHealth(x.health))
          ? current : accounts.find((x) => isPublishableHealth(x.health))?.id || '');
      try {
        const library = await apiJson('/api/native-social/instagram/clips');
        const availableClips = Array.isArray(library.clips) ? library.clips : [];
        setClips(availableClips);
        setSelectedClip((current) =>
          availableClips.some((x) => x.id === current) ? current : availableClips[0]?.id || '');
      } catch (_) {
        setClips([]);
      }
    } catch (_) {
      setConnections([]);
      setClips([]);
      setHistory([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let status = '';
    try {
      const params = new URLSearchParams(window.location.search);
      status = params.get('instagram_connect') || '';
      if (status) {
        params.delete('instagram_connect');
        const query = params.toString();
        window.history.replaceState(null, '', window.location.pathname + (query ? '?' + query : '') + window.location.hash);
      }
    } catch (_) {}
    if (status === 'success') setMessage('Instagram account connected.');
    else if (status === 'error') setMessage('Instagram connection failed. Check app configuration and try again.');
    load();
  }, [load]);

  const connect = async () => {
    setBusy(true);
    setMessage('');
    try {
      const data = await apiJson('/api/native-social/instagram/connect');
      if (!data.authorization_url) throw new Error('Missing authorization URL');
      window.location.assign(data.authorization_url);
    } catch (error) {
      setMessage(error?.detail || 'Could not start Instagram connection.');
      setBusy(false);
    }
  };

  const publish = async () => {
    if (!selectedConnection || !selectedClip) return;
    setBusy(true);
    setMessage('Submitting Reel to Instagram…');
    try {
      let result = await apiJson('/api/native-social/instagram/publish', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          connection_id: selectedConnection,
          clip_id: selectedClip,
          caption,
          idempotency_key: window.crypto.randomUUID(),
        }),
      });
      setMessage(`Publish queued. Job ID: ${result.job_id}`);
      for (let attempt = 0; attempt < 45 && !['published', 'failed', 'needs_review'].includes(result.status); attempt += 1) {
        await new Promise((resolve) => window.setTimeout(resolve, 3000));
        result = await apiJson('/api/native-social/instagram/publishes/' + encodeURIComponent(result.job_id));
        setMessage(`Instagram publish status: ${result.status}`);
      }
      if (result.status === 'published') setMessage('Reel published successfully.');
      else if (result.status === 'needs_review') setMessage('Instagram could not confirm the publish result. Review the account before retrying.');
      else if (result.status === 'failed') setMessage('Instagram rejected the Reel. Check the clip and account configuration.');
      else setMessage(`Publishing is still in progress. Job ID: ${result.job_id}`);
      await load();
    } catch (error) {
      setMessage(error?.detail || 'Could not publish this Reel.');
    } finally {
      setBusy(false);
    }
  };

  const reconcile = async (job) => {
    if (job.status !== 'needs_review') return;
    const published = window.confirm(
      'Check this account in Instagram first.\n\nOK: The Reel is visible in Instagram.\nCancel: Check whether it is absent.'
    );
    let payload;
    if (published) {
      const mediaId = window.prompt('Enter the Instagram media ID for the published Reel:');
      if (!mediaId || !mediaId.trim()) return;
      payload = { outcome: 'published', media_id: mediaId.trim() };
    } else {
      if (!window.confirm('Confirm that this Reel is NOT present in Instagram? This will not retry publishing.')) return;
      payload = { outcome: 'not_published' };
    }
    setBusy(true);
    try {
      await apiJson('/api/native-social/instagram/publishes/' + encodeURIComponent(job.job_id) + '/reconcile', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      setMessage(published ? 'Publish marked as confirmed.' : 'Marked as not published. No automatic retry was started.');
      await load();
    } catch (error) {
      setMessage(error?.detail || 'Could not reconcile this publish.');
    } finally {
      setBusy(false);
    }
  };

  const disconnect = async (id) => {
    if (!window.confirm('Disconnect this Instagram account? Its saved access token will be deleted.')) return;
    setBusy(true);
    try {
      await apiJson('/api/native-social/instagram/connections/' + encodeURIComponent(id), { method: 'DELETE' });
      await load();
      setMessage('Instagram account disconnected. Revoke app access in Instagram settings separately if needed.');
    } catch (error) {
      setMessage(error?.detail || 'Could not disconnect Instagram account.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="card p-6 mb-6">
      <div className="flex items-center justify-between gap-3 mb-2">
        <h2 className="text-base font-medium text-ink lowercase flex items-center gap-2">
          <Instagram size={17} className="text-brass" /> Native Instagram publishing
        </h2>
        <button onClick={connect} disabled={busy} className="btn-primary px-3 py-2 text-xs">
          {busy ? <Loader2 size={14} className="animate-spin" /> : <Link2 size={14} />}
          {connections.some((account) => account.health === 'reauth_required') ? 'Reconnect Instagram' : 'Connect Instagram'}
        </button>
      </div>
      <p className="text-xs text-muted leading-relaxed mb-4">
        Connect a professional Instagram Business or Creator account. Long-lived access tokens are refreshed automatically before expiry. If Meta revokes access or a token expires, reconnect the account. Select an archived clip and publish it as a Reel. Existing Upload-Post publishing remains separate.
      </p>
      {message && <p role="status" className="text-xs text-ink2 mb-3">{message}</p>}
      {loading ? (
        <div className="flex items-center gap-2 text-xs text-muted"><Loader2 size={14} className="animate-spin" /> Loading accounts…</div>
      ) : connections.length === 0 ? (
        <p className="text-xs text-muted">No native Instagram accounts connected.</p>
      ) : (
        <div className="space-y-3">
          {connections.map((account) => (
            <div key={account.id} className="flex items-center justify-between gap-3 border-t border-rule pt-3">
              <div className="min-w-0">
                <p className="text-sm text-ink truncate">@{account.username}</p>
                <p className="text-xs text-muted">{connectionHealthLabel(account.health)}</p>
              </div>
              <button onClick={() => disconnect(account.id)} disabled={busy}
                className="btn-quiet px-3 py-2 text-xs shrink-0">
                <Unlink size={13} /> Disconnect
              </button>
            </div>
          ))}
          <div className="border-t border-rule pt-4 space-y-3">
            <h3 className="text-sm font-medium text-ink">Publish a generated clip</h3>
            <label className="block text-xs text-muted">
              Instagram account
              <select value={selectedConnection} onChange={(e) => setSelectedConnection(e.target.value)}
                className="mt-1 w-full rounded-input border border-rule bg-paper2 px-3 py-2 text-sm text-ink">
                {connections.filter((a) => isPublishableHealth(a.health)).map((account) => (
                  <option key={account.id} value={account.id}>@{account.username}</option>
                ))}
              </select>
            </label>
            <label className="block text-xs text-muted">
              Generated clip
              <select value={selectedClip} onChange={(e) => setSelectedClip(e.target.value)}
                className="mt-1 w-full rounded-input border border-rule bg-paper2 px-3 py-2 text-sm text-ink">
                <option value="">Select a clip</option>
                {clips.map((clip) => (
                  <option key={clip.id} value={clip.id}>{clip.title} · {new Date(clip.created_at).toLocaleDateString()}</option>
                ))}
              </select>
            </label>
            <label className="block text-xs text-muted">
              Caption
              <textarea value={caption} onChange={(e) => setCaption(e.target.value)} maxLength={2200}
                rows={3} placeholder="Write your Reel caption…"
                className="mt-1 w-full rounded-input border border-rule bg-paper2 px-3 py-2 text-sm text-ink" />
            </label>
            <button onClick={publish} disabled={busy || !selectedConnection || !selectedClip}
              className="btn-primary px-3 py-2 text-xs disabled:opacity-50">
              {busy ? <Loader2 size={14} className="animate-spin" /> : <Instagram size={14} />}
              Publish Reel
            </button>
            {clips.length === 0 && <p className="text-xs text-muted">No archived clips are available. Generate a clip first; it will appear here after it is saved to your library.</p>}
          </div>
>

        </div>
      )}
<div className="border-t border-rule pt-4 space-y-3">
        <div className="flex items-center justify-between gap-2">
          <h3 className="text-sm font-medium text-ink">Publishing history</h3>
          <button type="button" onClick={load} disabled={busy} className="btn-quiet px-2 py-1 text-xs">Refresh</button>
        </div>
        {history.length === 0 ? (
          <p className="text-xs text-muted">No Instagram publishing activity yet.</p>
        ) : (
          <div className="space-y-2">
            {history.map((job) => (
              <div key={job.job_id} className="border-t border-rule pt-2">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="text-xs text-ink">{job.account ? '@' + job.account : 'Instagram'} · {job.status.replaceAll('_', ' ')}</p>
                    <p className="text-xs text-muted">{job.created_at ? new Date(job.created_at).toLocaleString() : ''}</p>
                    {job.caption && <p className="text-xs text-muted truncate">{job.caption}</p>}
                    {job.media_id && <p className="text-xs text-muted">Media ID: {job.media_id}</p>}
                    {job.error_code && <p className="text-xs text-muted">Code: {job.error_code}</p>}
                  </div>
                  {job.status === 'needs_review' && (
                    <button type="button" onClick={() => reconcile(job)} disabled={busy}
                      className="btn-primary px-2 py-1 text-xs shrink-0">Review</button>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
        </div
    </section>
  );
}
