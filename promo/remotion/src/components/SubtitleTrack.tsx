import {interpolate, useCurrentFrame} from 'remotion';

import type {SubtitleCue} from '../data/subtitles';
import {palette, typography} from '../theme';

export const SubtitleTrack = ({cues}: {cues: SubtitleCue[]}) => {
  const frame = useCurrentFrame();
  const current = cues.find((item) => frame >= item.from && frame < item.to);

  if (!current) {
    return null;
  }

  const local = frame - current.from;
  const opacity = interpolate(local, [0, 8], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  return (
    <div
      style={{
        position: 'absolute',
        zIndex: 100,
        left: 170,
        right: 170,
        bottom: 34,
        display: 'flex',
        justifyContent: 'center',
        opacity,
      }}
    >
      <div
        style={{
          maxWidth: 1480,
          padding: '18px 34px 20px',
          borderRadius: 24,
          color: palette.white,
          background: 'rgba(34,35,31,.92)',
          boxShadow: '0 16px 42px rgba(0,0,0,.2)',
          fontFamily: typography.sans,
          fontSize: 34,
          fontWeight: 700,
          lineHeight: 1.45,
          textAlign: 'center',
        }}
      >
        {current.text}
      </div>
    </div>
  );
};
