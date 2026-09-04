import {spring, useCurrentFrame, useVideoConfig} from 'remotion';

import {Brand} from '../components/Brand';
import {SceneFrame} from '../components/SceneFrame';
import {TelegramMark} from '../components/TelegramChat';
import {palette, shadow, typography} from '../theme';

const nodes = [
  {title: 'Telegram Bot', detail: '用户私聊入口', color: '#229ed9', icon: <TelegramMark size={70} />},
  {title: 'LangBot Bridge', detail: '可信事件桥接', color: palette.accent, icon: '↔'},
  {title: 'Notebook Agent', detail: '检索与权限归属', color: palette.green, icon: 'NA'},
];

export const TelegramIntroScene = ({durationInFrames}: {durationInFrames: number}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();

  return (
    <SceneFrame durationInFrames={durationInFrames} dark>
      <div style={{position: 'absolute', top: 54, left: 82}}><Brand compact light /></div>
      <div style={{marginTop: 96, textAlign: 'center'}}>
        <div style={{color: palette.yellow, fontFamily: typography.sans, fontSize: 22, fontWeight: 900, letterSpacing: '.16em'}}>TELEGRAM · READY</div>
        <div style={{marginTop: 14, fontFamily: typography.serif, fontSize: 76, fontWeight: 700}}>不必打开新应用，在 Telegram 里直接使用。</div>
      </div>
      <div style={{position: 'relative', marginTop: 82, display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 78}}>
        <div style={{position: 'absolute', left: 250, right: 250, top: 88, height: 5, background: 'linear-gradient(90deg, #229ed9, #bb4b2f, #315e53)', opacity: 0.72}} />
        {nodes.map((node, index) => {
          const enter = spring({frame: frame - index * 28, fps, config: {damping: 14, stiffness: 100}});
          return (
            <div key={node.title} style={{position: 'relative', zIndex: 2, padding: 34, border: `1px solid ${node.color}`, borderRadius: '36px 36px 36px 10px', background: 'rgba(255,255,255,.07)', boxShadow: shadow, textAlign: 'center', opacity: enter, transform: `translateY(${(1 - enter) * 52}px)`}}>
              <div style={{width: 104, height: 104, margin: '0 auto', display: 'grid', placeItems: 'center', borderRadius: 999, color: palette.white, background: node.icon === 'NA' ? palette.green : node.icon === '↔' ? palette.accent : 'transparent', fontFamily: typography.sans, fontSize: 34, fontWeight: 900}}>{node.icon}</div>
              <div style={{marginTop: 24, fontFamily: typography.serif, fontSize: 38, fontWeight: 700}}>{node.title}</div>
              <div style={{marginTop: 10, color: 'rgba(255,253,248,.66)', fontFamily: typography.sans, fontSize: 22}}>{node.detail}</div>
            </div>
          );
        })}
      </div>
      <div style={{marginTop: 48, color: 'rgba(255,253,248,.7)', fontFamily: typography.sans, fontSize: 25, textAlign: 'center'}}>普通消息与命令都会由 Notebook Agent 处理 · LangBot 不拥有知识权限</div>
    </SceneFrame>
  );
};
