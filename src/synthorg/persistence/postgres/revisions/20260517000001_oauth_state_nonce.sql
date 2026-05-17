-- depends: 20260515000001_ceremony_scheduler_state

-- OIDC nonce binding: persist the nonce generated at authorization-code
-- flow start so the callback can match it against the id_token ``nonce``
-- claim. Nullable: plain-OAuth2 connections (no jwks_uri) never set it.

ALTER TABLE oauth_states ADD COLUMN nonce TEXT;
