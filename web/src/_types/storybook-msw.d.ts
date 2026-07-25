/**
 * Pulls in msw-storybook-addon's ``StoryContext.msw`` augmentation so a
 * story's ``beforeEach({ msw })`` sees a typed ``SetupWorker`` instead of
 * ``any``.
 *
 * The addon publishes this augmentation on its ``./types`` subpath, which
 * maps to a declaration file with no runtime module, so the reference has to
 * live in type space where it is never emitted. It cannot go in
 * ``web/env.d.ts``: that file is a global script, and adding a top-level
 * import would turn it into a module and stop ``ImportMetaEnv`` from
 * augmenting ``import.meta.env`` app-wide. It also cannot go in
 * ``.storybook/preview.tsx``, which sits outside every tsconfig ``include``.
 *
 * Without this, ``tsc`` still passes (the context member resolves as ``any``)
 * and only the ``no-unsafe-call`` / ``no-unsafe-member-access`` lint rules
 * catch the loss of type safety.
 */
import 'msw-storybook-addon/types'
