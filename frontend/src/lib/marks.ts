// Analysis marks shown on workflow stages (root blocker, impact, correction preview).
// Kept free of Three.js so that importing them does not pull the 3D library into the main bundle.
export type SceneMark = 'root' | 'consequence' | 'downstream' | 'rerun' | 'kept';

export const MARK_TEXT: Record<SceneMark, string> = {
  root: 'Root blocker', consequence: 'Consequence', downstream: 'Downstream', rerun: 'Will re-evaluate', kept: 'Kept',
};
