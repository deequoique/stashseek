import type {CSSProperties, ReactNode} from 'react';

import {palette, shadow, typography} from '../theme';

export const TelegramMark = ({size = 56}: {size?: number}) => (
  <div
    style={{
      width: size,
      height: size,
      display: 'grid',
      placeItems: 'center',
      borderRadius: 999,
      color: palette.white,
      background: '#229ed9',
      fontFamily: typography.sans,
      fontSize: size * 0.48,
      fontWeight: 900,
      boxShadow: '0 10px 24px rgba(34,158,217,.25)',
    }}
  >
    ↗
  </div>
);

export const TelegramShell = ({children, style}: {children: ReactNode; style?: CSSProperties}) => (
  <div
    style={{
      overflow: 'hidden',
      border: '1px solid rgba(34,158,217,.22)',
      borderRadius: '38px 38px 38px 10px',
      background: '#dfeaf0',
      boxShadow: shadow,
      fontFamily: typography.sans,
      ...style,
    }}
  >
    <div style={{height: 86, display: 'flex', alignItems: 'center', gap: 18, padding: '0 28px', color: palette.white, background: '#229ed9'}}>
      <TelegramMark size={54} />
      <div>
        <div style={{fontSize: 25, fontWeight: 900}}>Notebook Agent</div>
        <div style={{marginTop: 3, fontSize: 17, opacity: 0.8}}>@notebook_agent_bot · Telegram</div>
      </div>
      <div style={{marginLeft: 'auto', fontSize: 30, letterSpacing: '.18em'}}>•••</div>
    </div>
    <div
      style={{
        position: 'relative',
        height: 'calc(100% - 86px)',
        padding: 28,
        backgroundImage: 'radial-gradient(circle at 12px 12px, rgba(49,94,83,.08) 2px, transparent 2px)',
        backgroundSize: '28px 28px',
      }}
    >
      {children}
    </div>
  </div>
);

interface TelegramBubbleProps {
  children: ReactNode;
  from: 'user' | 'bot';
  style?: CSSProperties;
}

export const TelegramBubble = ({children, from, style}: TelegramBubbleProps) => (
  <div
    style={{
      width: 'fit-content',
      maxWidth: '84%',
      marginLeft: from === 'user' ? 'auto' : 0,
      marginRight: from === 'bot' ? 'auto' : 0,
      padding: '17px 21px',
      borderRadius: from === 'user' ? '22px 22px 6px 22px' : '22px 22px 22px 6px',
      color: palette.ink,
      background: from === 'user' ? '#d7f7c7' : palette.white,
      boxShadow: '0 5px 16px rgba(30,48,58,.10)',
      fontSize: 22,
      lineHeight: 1.48,
      ...style,
    }}
  >
    {children}
  </div>
);
