/**
 * Runtime feature-flag matrix returned by ``GET /api/v1/capabilities``.
 *
 * Each boolean reports whether the matching optional subsystem is
 * wired in this deployment.  The dashboard reads this once per
 * session and skips polling endpoints whose flag is ``false`` so the
 * audit log stops collecting 503-spam from unconfigured services
 * (issue #1666 B-3).
 */
export interface Capabilities {
  /** Client-simulation runtime is configured. */
  simulations: boolean
  /** Request facade (depends on simulation state) is configured. */
  requests: boolean
  /** Ontology service is configured. */
  ontology: boolean
  /** Tunnel provider (pyngrok + auth token) is configured. */
  tunnel: boolean
  /** Webhook event bridge is configured. */
  webhooks: boolean
  /** A2A peer registry / client are configured. */
  a2a: boolean
  /**
   * Anonymous product telemetry is enabled and the reporter can
   * actually deliver.  ``true`` only when ``telemetry.enabled`` is
   * on AND the embedded token is present AND the backend SDK
   * configured successfully.
   */
  telemetry: boolean
  /**
   * The integrations subsystem is enabled at all -- when ``false``
   * connections / oauth / webhooks / mcp catalog routes are not
   * registered.
   */
  integrations: boolean
}
