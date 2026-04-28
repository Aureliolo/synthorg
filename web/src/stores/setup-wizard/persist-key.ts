/**
 * Side-effect-free constant for the setup-wizard persist key.
 *
 * Lives in its own module so the global test ``afterEach`` can read
 * the key without transitively loading ``@/api/client`` -- the broader
 * setup-wizard index pulls in providers / agents slices which do
 * import the API client and would defeat per-test ``vi.mock`` setups.
 */
export const SETUP_WIZARD_PERSIST_NAME = 'synthorg-setup-wizard-v1'
