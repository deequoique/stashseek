import {palette, typography} from '../theme';

interface SectionHeadingProps {
  eyebrow: string;
  title: string;
  copy?: string;
  light?: boolean;
  align?: 'left' | 'center';
}

export const SectionHeading = ({eyebrow, title, copy, light = false, align = 'left'}: SectionHeadingProps) => (
  <div style={{textAlign: align, maxWidth: align === 'center' ? 1420 : 980, margin: align === 'center' ? '0 auto' : 0}}>
    <div
      style={{
        color: light ? palette.yellow : palette.accentDark,
        fontFamily: typography.sans,
        fontSize: 22,
        fontWeight: 900,
        letterSpacing: '.16em',
        textTransform: 'uppercase',
      }}
    >
      {eyebrow}
    </div>
    <div
      style={{
        marginTop: 16,
        color: light ? palette.white : palette.ink,
        fontFamily: typography.serif,
        fontSize: 76,
        fontWeight: 700,
        lineHeight: 1.08,
        letterSpacing: '-0.045em',
      }}
    >
      {title}
    </div>
    {copy ? (
      <div
        style={{
          marginTop: 22,
          color: light ? 'rgba(255,253,248,.72)' : palette.muted,
          fontFamily: typography.sans,
          fontSize: 29,
          lineHeight: 1.58,
        }}
      >
        {copy}
      </div>
    ) : null}
  </div>
);
