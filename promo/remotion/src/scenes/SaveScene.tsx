import {spring, useCurrentFrame, useVideoConfig} from 'remotion';

import {Brand} from '../components/Brand';
import {SceneFrame} from '../components/SceneFrame';
import {SectionHeading} from '../components/SectionHeading';
import {TelegramBubble, TelegramShell} from '../components/TelegramChat';
import {palette, typography} from '../theme';

export const SaveScene = ({durationInFrames}: {durationInFrames: number}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const userIn = spring({frame: frame - 25, fps, config: {damping: 14, stiffness: 110}});
  const botIn = spring({frame: frame - 185, fps, config: {damping: 14, stiffness: 105}});

  return (
    <SceneFrame durationInFrames={durationInFrames}>
      <div style={{position: 'absolute', top: 54, left: 82}}><Brand compact /></div>
      <div style={{display: 'grid', gridTemplateColumns: '.76fr 1.24fr', alignItems: 'center', height: '100%', gap: 68}}>
        <div>
          <SectionHeading eyebrow="01 · SAVE IN TELEGRAM" title="发一句话，就能保存视频。" copy="明确说出保存意图并附上 YouTube 链接，Bot 会把任务加入处理队列。" />
          <div style={{marginTop: 34, padding: '16px 20px', borderLeft: `4px solid ${palette.yellow}`, color: palette.muted, background: 'rgba(214,169,66,.10)', fontFamily: typography.sans, fontSize: 21, lineHeight: 1.5}}>如果只发送裸链接，Bot 会先询问是否保存，再由你确认。</div>
        </div>

        <TelegramShell style={{height: 650}}>
          <TelegramBubble from="user" style={{opacity: userIn, transform: `translateY(${(1 - userIn) * 38}px)`, fontSize: 23}}>
            <strong>帮我保存这个视频</strong><br />
            想用简单方式理解神经网络<br />
            <span style={{color: '#1479ad'}}>https://youtu.be/aircAruvnKk</span>
          </TelegramBubble>
          <TelegramBubble from="bot" style={{marginTop: 24, opacity: botIn, transform: `translateY(${(1 - botIn) * 38}px)`, fontSize: 22}}>
            <strong>处理完成：共 1 个，已入队 1 个，失败 0 个。</strong><br />
            <span style={{color: palette.green}}>• [A1] 已加入处理队列。</span>
          </TelegramBubble>
          <div style={{position: 'absolute', left: 28, right: 28, bottom: 24, height: 58, display: 'flex', alignItems: 'center', padding: '0 20px', borderRadius: 999, color: palette.muted, background: palette.white, fontSize: 19}}>发送消息… <span style={{marginLeft: 'auto', color: '#229ed9', fontWeight: 900}}>➤</span></div>
        </TelegramShell>
      </div>
    </SceneFrame>
  );
};
