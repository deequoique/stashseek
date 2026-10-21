import {interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';

import {Brand} from '../components/Brand';
import {SceneFrame} from '../components/SceneFrame';
import {SectionHeading} from '../components/SectionHeading';
import {processSteps} from '../data/demo';
import {palette, shadow, typography} from '../theme';

export const ProcessingScene = ({durationInFrames}: {durationInFrames: number}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const progress = interpolate(frame, [35, 400], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});

  return (
    <SceneFrame durationInFrames={durationInFrames}>
      <div style={{position: 'absolute', top: 54, left: 82}}><Brand compact /></div>
      <SectionHeading eyebrow="02 · PROCESS" title="后台完成，过程清晰可见" copy="长内容异步解析；原文被保存、切分并建立可检索索引。" align="center" />

      <div style={{position: 'relative', marginTop: 90, display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 30}}>
        <div style={{position: 'absolute', top: 66, left: 150, right: 150, height: 6, borderRadius: 999, background: palette.line}}>
          <div style={{width: `${progress * 100}%`, height: '100%', borderRadius: 999, background: `linear-gradient(90deg, ${palette.accent}, ${palette.green})`}} />
        </div>
        {processSteps.map((step, index) => {
          const enter = spring({frame: frame - 35 - index * 75, fps, config: {damping: 15, stiffness: 100}});
          const complete = progress > (index + 0.7) / processSteps.length;
          return (
            <div key={step.title} style={{position: 'relative', zIndex: 2, textAlign: 'center', opacity: enter, transform: `translateY(${(1 - enter) * 50}px)`}}>
              <div
                style={{
                  width: 138,
                  height: 138,
                  margin: '0 auto',
                  display: 'grid',
                  placeItems: 'center',
                  borderRadius: 999,
                  border: `7px solid ${complete ? palette.green : palette.line}`,
                  color: complete ? palette.white : palette.ink,
                  background: complete ? palette.green : palette.paper,
                  boxShadow: shadow,
                  fontFamily: typography.sans,
                  fontSize: 38,
                  fontWeight: 900,
                }}
              >
                {complete ? '✓' : String(index + 1).padStart(2, '0')}
              </div>
              <div style={{marginTop: 30, fontFamily: typography.serif, fontSize: 36, fontWeight: 700}}>{step.title}</div>
              <div style={{marginTop: 12, color: palette.muted, fontFamily: typography.sans, fontSize: 23}}>{step.detail}</div>
            </div>
          );
        })}
      </div>

      <div style={{marginTop: 74, display: 'flex', justifyContent: 'center', gap: 18, fontFamily: typography.sans}}>
        {['Redis / Celery', 'PostgreSQL 全文检索', 'pgvector 语义检索'].map((label, index) => (
          <div key={label} style={{padding: '14px 24px', borderRadius: 999, color: index === 2 ? palette.white : palette.green, background: index === 2 ? palette.green : 'rgba(49,94,83,.08)', border: '1px solid rgba(49,94,83,.22)', fontSize: 21, fontWeight: 800}}>{label}</div>
        ))}
      </div>
    </SceneFrame>
  );
};
