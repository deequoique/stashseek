import {spring, useCurrentFrame, useVideoConfig} from 'remotion';

import {Brand} from '../components/Brand';
import {SceneFrame} from '../components/SceneFrame';
import {palette, shadow, typography} from '../theme';

export const CompanionScene = ({durationInFrames}: {durationInFrames: number}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const browserIn = spring({frame: frame - 8, fps, config: {damping: 16, stiffness: 92}});
  const popupIn = spring({frame: frame - 95, fps, config: {damping: 13, stiffness: 105}});
  const submitted = frame > 285;

  return (
    <SceneFrame durationInFrames={durationInFrames}>
      <div style={{position: 'absolute', top: 54, left: 82}}><Brand compact /></div>
      <div style={{display: 'flex', justifyContent: 'space-between', alignItems: 'end', marginTop: 40}}>
        <div>
          <div style={{color: palette.accentDark, fontFamily: typography.sans, fontSize: 22, fontWeight: 900, letterSpacing: '.16em'}}>03 · BROWSER COMPANION</div>
          <div style={{marginTop: 12, fontFamily: typography.serif, fontSize: 68, fontWeight: 700}}>当前页面的字幕，也能一键入库。</div>
        </div>
        <div style={{maxWidth: 420, color: palette.muted, fontFamily: typography.sans, fontSize: 23, lineHeight: 1.5}}>适合 NTULearn/Kaltura，也能在服务器访问 YouTube 受限时使用。</div>
      </div>

      <div style={{position: 'relative', marginTop: 46, height: 650}}>
        <div style={{position: 'absolute', inset: '0 250px 0 0', overflow: 'hidden', border: `1px solid ${palette.line}`, borderRadius: 30, background: palette.white, boxShadow: shadow, opacity: browserIn, transform: `translateY(${(1 - browserIn) * 40}px)`}}>
          <div style={{height: 64, display: 'flex', alignItems: 'center', gap: 12, padding: '0 22px', background: '#ebe8e1', fontFamily: typography.sans}}>
            <span style={{width: 12, height: 12, borderRadius: 999, background: palette.accent}} />
            <span style={{width: 12, height: 12, borderRadius: 999, background: palette.yellow}} />
            <span style={{width: 12, height: 12, borderRadius: 999, background: palette.green}} />
            <div style={{marginLeft: 16, flex: 1, padding: '10px 18px', borderRadius: 999, color: palette.muted, background: palette.white, fontSize: 17}}>ntulearn.ntu.edu.sg / course / video</div>
          </div>
          <div style={{display: 'grid', gridTemplateColumns: '1.4fr .6fr', height: 586}}>
            <div style={{position: 'relative', display: 'grid', placeItems: 'center', background: 'linear-gradient(145deg, #191b18, #315e53)'}}>
              <div style={{width: 110, height: 110, display: 'grid', placeItems: 'center', border: '2px solid rgba(255,255,255,.6)', borderRadius: 999, color: palette.white, fontSize: 44}}>▶</div>
              <div style={{position: 'absolute', left: 34, bottom: 34, color: palette.white, fontFamily: typography.sans, fontSize: 24}}>Lecture 08 · Neural Networks</div>
            </div>
            <div style={{padding: 34, fontFamily: typography.sans}}>
              <div style={{color: palette.accentDark, fontSize: 18, fontWeight: 900, letterSpacing: '.12em'}}>NTULEARN</div>
              <div style={{marginTop: 15, fontFamily: typography.serif, fontSize: 34, fontWeight: 700}}>Course video</div>
              <div style={{marginTop: 22, color: palette.muted, fontSize: 20, lineHeight: 1.55}}>当前浏览器已经获得字幕访问权限。</div>
            </div>
          </div>
        </div>

        <div style={{position: 'absolute', top: 46, right: 0, width: 420, padding: 28, border: `1px solid ${palette.line}`, borderRadius: 22, background: '#f8f4eb', boxShadow: shadow, opacity: popupIn, transform: `translateX(${(1 - popupIn) * 70}px)`, fontFamily: typography.sans}}>
          <div style={{color: '#8a6543', fontFamily: 'ui-monospace, monospace', fontSize: 14, fontWeight: 800, letterSpacing: '.16em'}}>NOTEBOOK AGENT</div>
          <div style={{marginTop: 9, fontFamily: typography.serif, fontSize: 36, fontWeight: 700}}>浏览器伴侣</div>
          <div style={{minHeight: 72, marginTop: 18, color: palette.muted, fontSize: 20, lineHeight: 1.5}}>{submitted ? '已提交到资料库，Notebook Agent 正在整理字幕。' : '已连接。打开一个视频，然后保存当前页面的字幕。'}</div>
          <div style={{marginTop: 20, padding: '16px 20px', borderRadius: 12, color: palette.white, background: submitted ? palette.green : '#765333', textAlign: 'center', fontSize: 21, fontWeight: 850}}>{submitted ? '已提交 ✓' : '保存当前视频'}</div>
          <div style={{marginTop: 18, paddingTop: 16, borderTop: `1px solid ${palette.line}`, color: palette.muted, fontSize: 15, lineHeight: 1.5}}>只提交字幕和公开元数据，不上传登录状态或音视频。</div>
        </div>
      </div>
    </SceneFrame>
  );
};
