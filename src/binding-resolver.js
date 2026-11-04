// C3 (SCRUM-22): turns a registered-gesture decision into an allowed mouse action.
// Pure functions only: no storage, no camera, no OS calls. The caller (app shell) decides when to run the action.

// Same shape as validGestureId in personalization.js. Control does not import recognition (docs/design/02-components.md);
// tests/binding-resolver.test.js checks that the two stay in agreement.
const GESTURE_ID = /^(?:builtin\.[a-z0-9]+(?:-[a-z0-9]+)*|custom:[A-Za-z0-9][A-Za-z0-9_-]{0,47})$/;

// The single place where the allowed mouse actions are defined. Action ids are not gesture ids:
// they use the dotted `mouse.*` form that SettingsStore.setBinding validates.
// Not offered on purpose: scroll and drag need a continuous hand movement, a static pose cannot drive them.
export const MOUSE_ACTIONS = Object.freeze({
  'mouse.left-click': Object.freeze({kind: 'events', label: '왼쪽 클릭'}),
  'mouse.right-click': Object.freeze({kind: 'events', label: '오른쪽 클릭'}),
  'mouse.pause': Object.freeze({kind: 'control', label: '제어 일시정지'})
});
export const ALLOWED_ACTION_IDS = Object.freeze(Object.keys(MOUSE_ACTIONS));
export const BINDING_REASONS = Object.freeze(['bound', 'not-accepted', 'no-binding', 'disabled', 'unknown-action']);

const own = (object, key) => object !== null && typeof object === 'object' && Object.hasOwn(object, key);
const record = value => value !== null && typeof value === 'object' && !Array.isArray(value);

function actionSet(allowedActions) {
  if (allowedActions instanceof Set) return allowedActions;
  if (Array.isArray(allowedActions)) return new Set(allowedActions);
  return new Set();
}

// decision: result of classifyRegisteredGesture. bindings: environments.<env>.bindings of the settings record
// (gestureId -> {gestureId, actionId, enabled}). allowedActions: array or Set of action ids.
// Never throws. A decision that was not accepted never becomes a command and is never replaced by another model.
export function resolveBinding(input) {
  const none = reason => ({actionId: null, reason});
  try {
    if (!record(input)) return none('not-accepted');
    const {decision, bindings, allowedActions} = input;
    if (!record(decision) || decision.accepted !== true || decision.kind !== 'match' || typeof decision.gestureId !== 'string' || !GESTURE_ID.test(decision.gestureId)) return none('not-accepted');
    if (!record(bindings) || !own(bindings, decision.gestureId)) return none('no-binding');
    const binding = bindings[decision.gestureId];
    if (!record(binding) || binding.gestureId !== decision.gestureId || typeof binding.enabled !== 'boolean') return none('no-binding');
    if (binding.enabled === false) return none('disabled');
    if (typeof binding.actionId !== 'string' || !own(MOUSE_ACTIONS, binding.actionId) || !actionSet(allowedActions).has(binding.actionId)) return none('unknown-action');
    return {actionId: binding.actionId, reason: 'bound'};
  } catch {
    return none('not-accepted');
  }
}

// Mouse events for an event action, in the format AirMouseController emits and desktop/security.mjs validates.
// Control actions (pause) have no events: the caller turns mouse control off. Unknown id -> null.
export function eventsForAction(actionId) {
  if (typeof actionId !== 'string' || !own(MOUSE_ACTIONS, actionId)) return null;
  if (actionId === 'mouse.left-click') return [{type: 'button', button: 'left', down: true}, {type: 'button', button: 'left', down: false}];
  if (actionId === 'mouse.right-click') return [{type: 'button', button: 'right', down: true}, {type: 'button', button: 'right', down: false}];
  return [];
}

// Duplicate policy: one binding per gesture (the record is keyed by gestureId), and several gestures MAY share
// an action. A frame is classified to at most one gesture, so a shared action never fires twice from one frame.
// The UI can warn about sharing with this report; it is information, not an error.
export function findSharedActions(bindings) {
  if (!record(bindings)) return [];
  const byAction = new Map();
  for (const [gestureId, binding] of Object.entries(bindings)) {
    if (!record(binding) || binding.enabled !== true || typeof binding.actionId !== 'string') continue;
    byAction.set(binding.actionId, [...(byAction.get(binding.actionId) ?? []), gestureId]);
  }
  return [...byAction].filter(([, ids]) => ids.length > 1).map(([actionId, gestureIds]) => ({actionId, gestureIds: gestureIds.sort()}));
}
