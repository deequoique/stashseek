import {Img, staticFile} from 'remotion';

import {palette, typography} from '../theme';

interface BrandProps {
  compact?: boolean;
  light?: boolean;
}

export const Brand = ({compact = false, light = false}: BrandProps) => (
  <div style={{display: 'flex', alignItems: 'center', gap: compact ? 18 : 26}}>
    <Img
      src={staticFile('assets/notebook-agent-logo.png')}
      style={{width: compact ? 70 : 104, height: compact ? 70 : 104, objectFit: 'contain'}}
    />
    <div
      style={{
        fontFamily: typography.sans,
        color: light ? palette.white : palette.ink,
        fontWeight: 850,
        fontSize: compact ? 31 : 54,
        letterSpacing: '-0.04em',
      }}
    >
      Notebook Agent
    </div>
  </div>
);
