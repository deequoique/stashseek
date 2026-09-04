import {spring, useCurrentFrame, useVideoConfig} from 'remotion';

import {Brand} from '../components/Brand';
import {SceneFrame} from '../components/SceneFrame';
import {palette, typography} from '../theme';

export const EndingScene = ({durationInFrames}: {durationInFrames: number}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const enter = spring({frame, fps, config: {damping: 15, stiffness: 85}});

  return (
    <SceneFrame durationInFrames={durationInFrames} style={{display: 'grid', placeItems: 'center', textAlign: 'center'}}>
      <div style={{opacity: enter, transform: `translateY(${(1 - enter) * 45}px) scale(${0.92 + enter * 0.08})`}}>
        <div style={{display: 'flex', justifyContent: 'center'}}><Brand /></div>
        <div style={{marginTop: 44, fontFamily: typography.serif, fontSize: 76, fontWeight: 700, letterSpacing: '-.04em'}}>让看过的内容，真正成为你的知识。</div>
        <div style={{marginTop: 34, display: 'flex', justifyContent: 'center', gap: 14, fontFamily: typography.sans}}>
          {['Telegram', 'Web', 'MCP', '浏览器伴侣'].map((item) => (
            <div key={item} style={{padding: '13px 22px', border: `1px solid ${palette.line}`, borderRadius: 999, color: palette.green, background: 'rgba(255,255,255,.35)', fontSize: 21, fontWeight: 850}}>{item}</div>
          ))}
        </div>
        <div style={{marginTop: 30, color: palette.muted, fontFamily: typography.sans, fontSize: 20}}>从聊天到浏览器，接入同一份私人资料库 · EAZO Global Hackathon Project</div>
      </div>
    </SceneFrame>
  );
};
