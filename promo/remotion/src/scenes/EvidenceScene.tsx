import {interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';

import {Brand} from '../components/Brand';
import {SceneFrame} from '../components/SceneFrame';
import {TelegramBubble, TelegramShell} from '../components/TelegramChat';
import {palette, typography} from '../theme';

const answer = '可以先把它理解成一个会调参数的数字转换器：输入很多数字，经过多层变换，最后输出一组判断结果。';

export const EvidenceScene = ({durationInFrames}: {durationInFrames: number}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const chars = Math.floor(interpolate(frame, [20, 155], [0, answer.length], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'}));
  const sourcesIn = spring({frame: frame - 180, fps, config: {damping: 15, stiffness: 100}});
  const calloutIn = spring({frame: frame - 360, fps, config: {damping: 14, stiffness: 100}});

  return (
    <SceneFrame durationInFrames={durationInFrames}>
      <div style={{position: 'absolute', top: 54, left: 82}}><Brand compact /></div>
      <div style={{display: 'grid', gridTemplateColumns: '.62fr 1.38fr', alignItems: 'center', height: '100%', gap: 56}}>
        <div>
          <div style={{color: palette.accentDark, fontFamily: typography.sans, fontSize: 22, fontWeight: 900, letterSpacing: '.16em'}}>05 · ANSWER WITH SOURCES</div>
          <div style={{marginTop: 18, fontFamily: typography.serif, fontSize: 76, fontWeight: 700, lineHeight: 1.06}}>回答和来源，都回到 Telegram。</div>
          <div style={{marginTop: 28, color: palette.muted, fontFamily: typography.sans, fontSize: 27, lineHeight: 1.55}}>服务器只附加本轮检索到的证据，并保留可跳转的原视频时间点。</div>
          <div style={{marginTop: 34, padding: '18px 22px', borderRadius: 18, color: palette.green, background: 'rgba(49,94,83,.09)', fontFamily: typography.sans, fontSize: 22, fontWeight: 850, opacity: calloutIn, transform: `translateY(${(1 - calloutIn) * 28}px)`}}>点击 04:03 → 回到对应原视频位置</div>
        </div>

        <TelegramShell style={{height: 720}}>
          <TelegramBubble from="bot" style={{maxWidth: '96%', fontSize: 21}}>
            <div style={{fontFamily: typography.serif, fontSize: 28, fontWeight: 700, lineHeight: 1.55}}>{answer.slice(0, chars)}</div>
            <div style={{marginTop: 20, opacity: sourcesIn}}>
              <strong>来源：</strong>
              <div style={{marginTop: 10, fontWeight: 800}}>But what is a neural network?</div>
              <div style={{marginTop: 9, padding: '11px 14px', borderLeft: '4px solid #229ed9', borderRadius: 8, background: 'rgba(34,158,217,.07)'}}>
                <span style={{color: '#1479ad', fontWeight: 850}}>[S12] 03:08 · youtu.be/aircAruvnKk?t=188</span><br />
                <span style={{color: palette.muted}}>28×28 像素如何成为 784 个输入</span>
              </div>
              <div style={{marginTop: 9, padding: '11px 14px', borderLeft: '4px solid #229ed9', borderRadius: 8, background: 'rgba(34,158,217,.07)'}}>
                <span style={{color: '#1479ad', fontWeight: 850}}>[S18] 04:03 · youtu.be/aircAruvnKk?t=243</span><br />
                <span style={{color: palette.muted}}>隐藏层与逐层激活</span>
              </div>
              <div style={{marginTop: 9, padding: '11px 14px', borderLeft: '4px solid #229ed9', borderRadius: 8, background: 'rgba(34,158,217,.07)'}}>
                <span style={{color: '#1479ad', fontWeight: 850}}>[S31] 15:39 · youtu.be/aircAruvnKk?t=939</span><br />
                <span style={{color: palette.muted}}>把整个网络理解成一个函数</span>
              </div>
            </div>
          </TelegramBubble>
          <div style={{position: 'absolute', left: 28, right: 28, bottom: 24, height: 58, display: 'flex', alignItems: 'center', padding: '0 20px', borderRadius: 999, color: palette.muted, background: palette.white, fontSize: 19}}>继续提问… <span style={{marginLeft: 'auto', color: '#229ed9', fontWeight: 900}}>➤</span></div>
        </TelegramShell>
      </div>
    </SceneFrame>
  );
};
