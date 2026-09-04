import {Audio} from '@remotion/media';
import {AbsoluteFill, Sequence, staticFile} from 'remotion';

import {SubtitleTrack} from './components/SubtitleTrack';
import {subtitleCues} from './data/subtitles';
import {EndingScene} from './scenes/EndingScene';
import {EvidenceScene} from './scenes/EvidenceScene';
import {CompanionScene} from './scenes/CompanionScene';
import {OpeningScene} from './scenes/OpeningScene';
import {PrivacyScene} from './scenes/PrivacyScene';
import {ProcessingScene} from './scenes/ProcessingScene';
import {QuestionScene} from './scenes/QuestionScene';
import {SaveScene} from './scenes/SaveScene';
import {TelegramIntroScene} from './scenes/TelegramIntroScene';
import {palette} from './theme';
import {scenes, toFrames} from './timeline';

export interface NotebookAgentPromoProps {
  narrationSrc?: string;
  musicSrc?: string;
}

const sceneComponents = {
  opening: OpeningScene,
  telegramIntro: TelegramIntroScene,
  save: SaveScene,
  processing: ProcessingScene,
  companion: CompanionScene,
  question: QuestionScene,
  evidence: EvidenceScene,
  privacy: PrivacyScene,
  ending: EndingScene,
} as const;

export const NotebookAgentPromo = ({narrationSrc, musicSrc}: NotebookAgentPromoProps) => (
  <AbsoluteFill style={{background: palette.ink}}>
    {scenes.map((scene) => {
      const Scene = sceneComponents[scene.id];
      const durationInFrames = toFrames(scene.duration);
      return (
        <Sequence key={scene.id} from={toFrames(scene.start)} durationInFrames={durationInFrames} premountFor={30}>
          <Scene durationInFrames={durationInFrames} />
        </Sequence>
      );
    })}

    {musicSrc ? <Audio src={staticFile(musicSrc)} volume={0.12} loop /> : null}
    {narrationSrc ? <Audio src={staticFile(narrationSrc)} volume={1} /> : null}
    <SubtitleTrack cues={subtitleCues} />
  </AbsoluteFill>
);
