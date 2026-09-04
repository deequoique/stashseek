import {spring, useCurrentFrame, useVideoConfig} from 'remotion';

import {Brand} from '../components/Brand';
import {SceneFrame} from '../components/SceneFrame';
import {TelegramMark} from '../components/TelegramChat';
import {palette, shadow, typography} from '../theme';

export const PrivacyScene = ({durationInFrames}: {durationInFrames: number}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();

  return (
    <SceneFrame durationInFrames={durationInFrames} dark>
      <div style={{position: 'absolute', top: 54, left: 82}}><Brand compact light /></div>
      <div style={{display: 'grid', gridTemplateColumns: '.74fr 1.26fr', alignItems: 'center', height: '100%', gap: 76}}>
        <div>
          <div style={{color: palette.yellow, fontFamily: typography.sans, fontSize: 22, fontWeight: 900, letterSpacing: '.16em'}}>06 · LINK & ISOLATE</div>
          <div style={{marginTop: 18, fontFamily: typography.serif, fontSize: 74, fontWeight: 700, lineHeight: 1.06}}>Telegram 和 Web，共用同一份私人资料库。</div>
          <div style={{marginTop: 28, color: 'rgba(255,253,248,.7)', fontFamily: typography.sans, fontSize: 27, lineHeight: 1.55}}>一次性绑定码合并账户；两个渠道的对话历史仍然分别保存。</div>
        </div>

        <div style={{position: 'relative', height: 600, display: 'grid', gridTemplateColumns: '1fr 1fr', alignItems: 'center', gap: 130}}>
          <div style={{position: 'absolute', left: 300, right: 300, top: 298, height: 5, background: 'linear-gradient(90deg, #229ed9, #d6a942, #315e53)'}} />
          {[
            {title: 'Telegram', detail: '发送 /link web', color: '#229ed9', icon: <TelegramMark size={88} />},
            {title: 'Web 资料库', detail: '粘贴一次性绑定码', color: palette.green, icon: 'WEB'},
          ].map((node, index) => {
            const enter = spring({frame: frame - index * 32, fps, config: {damping: 15, stiffness: 98}});
            return (
              <div key={node.title} style={{position: 'relative', zIndex: 2, padding: 42, border: `1px solid ${node.color}`, borderRadius: '38px 38px 38px 10px', background: 'rgba(255,255,255,.08)', boxShadow: shadow, textAlign: 'center', opacity: enter, transform: `translateY(${(1 - enter) * 48}px)`, fontFamily: typography.sans}}>
                <div style={{width: 112, height: 112, margin: '0 auto', display: 'grid', placeItems: 'center', borderRadius: 999, color: palette.white, background: node.title === 'Telegram' ? 'transparent' : palette.green, fontSize: 28, fontWeight: 900}}>{node.icon}</div>
                <div style={{marginTop: 24, fontFamily: typography.serif, fontSize: 40, fontWeight: 700}}>{node.title}</div>
                <div style={{marginTop: 12, color: node.color === '#229ed9' ? '#8ed7f8' : '#99cfbf', fontSize: 22, fontWeight: 850}}>{node.detail}</div>
              </div>
            );
          })}
          <div style={{position: 'absolute', zIndex: 3, left: '50%', top: '50%', width: 126, height: 126, display: 'grid', placeItems: 'center', border: `6px solid ${palette.yellow}`, borderRadius: 999, color: palette.ink, background: palette.yellow, transform: 'translate(-50%, -50%)', fontFamily: typography.sans, fontSize: 44}}>🔒</div>
        </div>
      </div>
    </SceneFrame>
  );
};
