import {spring, useCurrentFrame, useVideoConfig} from 'remotion';

import {Brand} from '../components/Brand';
import {SceneFrame} from '../components/SceneFrame';
import {TelegramBubble, TelegramShell} from '../components/TelegramChat';
import {palette, typography} from '../theme';

export const QuestionScene = ({durationInFrames}: {durationInFrames: number}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const userIn = spring({frame: frame - 20, fps, config: {damping: 14, stiffness: 115}});
  const typingIn = spring({frame: frame - 160, fps, config: {damping: 16, stiffness: 100}});

  return (
    <SceneFrame durationInFrames={durationInFrames} dark>
      <div style={{position: 'absolute', top: 54, left: 82}}><Brand compact light /></div>
      <div style={{display: 'grid', gridTemplateColumns: '.72fr 1.28fr', alignItems: 'center', height: '100%', gap: 72}}>
        <div>
          <div style={{color: palette.yellow, fontFamily: typography.sans, fontSize: 22, fontWeight: 900, letterSpacing: '.16em'}}>04 · ASK IN TELEGRAM</div>
          <div style={{marginTop: 18, fontFamily: typography.serif, fontSize: 80, fontWeight: 700, lineHeight: 1.06}}>还是在同一个 Bot 里，直接提问。</div>
          <div style={{marginTop: 28, color: 'rgba(255,253,248,.7)', fontFamily: typography.sans, fontSize: 28, lineHeight: 1.55}}>对话由 Telegram 进入，检索范围仍然只属于当前账户。</div>
        </div>

        <TelegramShell style={{height: 620}}>
          <TelegramBubble from="user" style={{opacity: userIn, transform: `translateY(${(1 - userIn) * 40}px)`, fontFamily: typography.serif, fontSize: 31, fontWeight: 700}}>
            用高中生能理解的方式解释：<br />神经网络到底在做什么？
          </TelegramBubble>
          <TelegramBubble from="bot" style={{marginTop: 24, opacity: typingIn, transform: `translateY(${(1 - typingIn) * 30}px)`, color: palette.muted, fontSize: 24, letterSpacing: '.18em'}}>
            正在检索你的资料库 ···
          </TelegramBubble>
          <div style={{position: 'absolute', left: 28, right: 28, bottom: 24, height: 58, display: 'flex', alignItems: 'center', padding: '0 20px', borderRadius: 999, color: palette.muted, background: palette.white, fontSize: 19}}>发送消息… <span style={{marginLeft: 'auto', color: '#229ed9', fontWeight: 900}}>➤</span></div>
        </TelegramShell>
      </div>
    </SceneFrame>
  );
};
