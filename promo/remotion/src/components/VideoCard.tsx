import type {CSSProperties} from 'react';

import {palette, shadow, typography} from '../theme';

interface VideoCardProps {
  title: string;
  creator: string;
  tag: string;
  accent: string;
  style?: CSSProperties;
}

export const VideoCard = ({title, creator, tag, accent, style}: VideoCardProps) => (
  <div
    style={{
      width: 540,
      padding: 24,
      display: 'grid',
      gridTemplateColumns: '164px 1fr',
      gap: 24,
      borderRadius: '30px 30px 30px 8px',
      border: `1px solid ${palette.line}`,
      background: 'rgba(251,248,241,.98)',
      boxShadow: shadow,
      color: palette.ink,
      fontFamily: typography.sans,
      ...style,
    }}
  >
    <div
      style={{
        height: 126,
        borderRadius: '20px 20px 20px 6px',
        background: `linear-gradient(145deg, ${accent}, ${palette.ink})`,
        position: 'relative',
        overflow: 'hidden',
      }}
    >
      <div style={{position: 'absolute', inset: 22, border: '2px solid rgba(255,255,255,.45)', borderRadius: 999}} />
      <div style={{position: 'absolute', width: 0, height: 0, left: 68, top: 45, borderTop: '18px solid transparent', borderBottom: '18px solid transparent', borderLeft: '28px solid white'}} />
    </div>
    <div style={{display: 'flex', flexDirection: 'column', justifyContent: 'center', minWidth: 0}}>
      <div style={{color: palette.accentDark, fontSize: 20, fontWeight: 850, letterSpacing: '.08em'}}>{tag}</div>
      <div style={{marginTop: 10, fontSize: 28, fontWeight: 850, lineHeight: 1.18}}>{title}</div>
      <div style={{marginTop: 10, color: palette.muted, fontSize: 20}}>{creator}</div>
    </div>
  </div>
);
