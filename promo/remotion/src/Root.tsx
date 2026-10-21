import {Composition} from 'remotion';

import {NotebookAgentPromo} from './NotebookAgentPromo';
import {durationInFrames, FPS, HEIGHT, WIDTH} from './timeline';

export const RemotionRoot = () => (
  <Composition
    id="NotebookAgentLandscape"
    component={NotebookAgentPromo}
    durationInFrames={durationInFrames}
    fps={FPS}
    width={WIDTH}
    height={HEIGHT}
    defaultProps={{}}
  />
);
