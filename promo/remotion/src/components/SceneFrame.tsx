import type {CSSProperties, ReactNode} from 'react';
import {AbsoluteFill, interpolate, useCurrentFrame} from 'remotion';

import {paperBackground, safeArea} from '../theme';

interface SceneFrameProps {
  children: ReactNode;
  durationInFrames: number;
  dark?: boolean;
  style?: CSSProperties;
}

export const SceneFrame = ({children, durationInFrames, dark = false, style}: SceneFrameProps) => {
  const frame = useCurrentFrame();
  const fadeIn = interpolate(frame, [0, 16], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  const fadeOut = interpolate(frame, [durationInFrames - 16, durationInFrames], [1, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  return (
    <AbsoluteFill
      style={{
        ...paperBackground,
        ...safeArea,
        overflow: 'hidden',
        opacity: Math.min(fadeIn, fadeOut),
        color: dark ? '#fffdf8' : '#22231f',
        backgroundColor: dark ? '#22231f' : '#f3efe6',
        backgroundImage: dark
          ? 'radial-gradient(circle at 18% 18%, rgba(187,75,47,.26), transparent 34%), radial-gradient(circle at 82% 78%, rgba(49,94,83,.32), transparent 34%)'
          : paperBackground.backgroundImage,
        ...style,
      }}
    >
      {children}
    </AbsoluteFill>
  );
};
