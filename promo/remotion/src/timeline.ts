export const FPS = 30;
export const WIDTH = 1920;
export const HEIGHT = 1080;

export const scenes = [
  {id: 'opening', start: 0, duration: 10},
  {id: 'telegramIntro', start: 10, duration: 10},
  {id: 'save', start: 20, duration: 14},
  {id: 'processing', start: 34, duration: 16},
  {id: 'companion', start: 50, duration: 16},
  {id: 'question', start: 66, duration: 12},
  {id: 'evidence', start: 78, duration: 22},
  {id: 'privacy', start: 100, duration: 13},
  {id: 'ending', start: 113, duration: 7},
] as const;

export const durationInFrames = 120 * FPS;

export const toFrames = (seconds: number) => Math.round(seconds * FPS);
