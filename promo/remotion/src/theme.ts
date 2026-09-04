import type {CSSProperties} from 'react';

export const palette = {
  ink: '#22231f',
  muted: '#6e6b63',
  paper: '#fbf8f1',
  canvas: '#f3efe6',
  paperDeep: '#ece5d8',
  line: '#d6cfc2',
  accent: '#bb4b2f',
  accentDark: '#843520',
  green: '#315e53',
  yellow: '#d6a942',
  white: '#fffdf8',
} as const;

export const typography = {
  sans: 'Inter, "PingFang SC", "Noto Sans CJK SC", "Microsoft YaHei", sans-serif',
  serif: 'Georgia, "Songti SC", "Noto Serif CJK SC", serif',
} as const;

export const safeArea: CSSProperties = {
  padding: '76px 108px 118px',
};

export const shadow = '0 28px 70px rgba(57, 48, 36, 0.14)';

export const paperBackground: CSSProperties = {
  backgroundColor: palette.canvas,
  backgroundImage: [
    'radial-gradient(circle at 14% 12%, rgba(214,169,66,.18), transparent 30%)',
    'radial-gradient(circle at 88% 82%, rgba(49,94,83,.14), transparent 32%)',
    'repeating-linear-gradient(0deg, rgba(74,65,49,.035) 0, rgba(74,65,49,.035) 1px, transparent 1px, transparent 34px)',
  ].join(','),
};
