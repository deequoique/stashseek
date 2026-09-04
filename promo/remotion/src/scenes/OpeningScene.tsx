import {Img, interpolate, spring, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import {siBilibili, siTiktok, siXiaohongshu, siYoutube} from 'simple-icons/icons';
import type {SimpleIcon} from 'simple-icons';

import {SceneFrame} from '../components/SceneFrame';
import {SectionHeading} from '../components/SectionHeading';
import {VideoCard} from '../components/VideoCard';
import {demoVideos} from '../data/demo';
import {palette, shadow, typography} from '../theme';

interface PlatformLogoProps {
  icon: SimpleIcon;
  label: string;
  left: number;
  top: number;
  frame: number;
  delay: number;
  fps: number;
}

const PlatformLogo = ({icon, label, left, top, frame, delay, fps}: PlatformLogoProps) => {
  const enter = spring({frame: frame - delay, fps, config: {damping: 15, stiffness: 105}});
  const drift = Math.sin((frame + delay * 2) / 22) * 8;

  return (
    <div
      style={{
        position: 'absolute',
        zIndex: 5,
        left,
        top,
        width: 118,
        height: 118,
        display: 'grid',
        placeItems: 'center',
        border: '1px solid rgba(255,255,255,.16)',
        borderRadius: 30,
        background: palette.white,
        boxShadow: shadow,
        opacity: enter,
        transform: `translateY(${(1 - enter) * 45 + drift}px) scale(${0.84 + enter * 0.16})`,
        fontFamily: typography.sans,
      }}
    >
      <svg viewBox="0 0 24 24" style={{width: 56, height: 56}} aria-label={label}>
        <path d={icon.path} fill={`#${icon.hex}`} />
      </svg>
      <div style={{position: 'absolute', bottom: 11, left: 8, right: 8, color: palette.ink, fontSize: 14, fontWeight: 850, textAlign: 'center'}}>{label}</div>
    </div>
  );
};

export const OpeningScene = ({durationInFrames}: {durationInFrames: number}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const problemOpacity = interpolate(frame, [135, 165], [1, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  const promiseOpacity = interpolate(frame, [170, 205], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  const logo = spring({frame: frame - 174, fps, config: {damping: 13, stiffness: 94}});

  return (
    <SceneFrame durationInFrames={durationInFrames} dark>
      <div style={{position: 'absolute', inset: 0, padding: '76px 108px 118px', opacity: problemOpacity}}>
        <div style={{display: 'grid', gridTemplateColumns: '1.02fr .98fr', height: '100%', alignItems: 'center', gap: 56}}>
          <div>
            <SectionHeading
              eyebrow="THE PROBLEM"
              title="收藏了很多，真正找得到的有多少？"
              copy="课程、访谈和演讲散落在不同平台；真正需要时，却只剩下一个模糊的印象。"
              light
            />
          </div>

          <div style={{position: 'relative', height: 720}}>
            {demoVideos.map((video, index) => {
              const enter = spring({frame: frame - index * 14, fps, config: {damping: 15, stiffness: 105}});
              return (
                <VideoCard
                  key={video.title}
                  {...video}
                  accent={[palette.accent, palette.green, palette.yellow][index]}
                  style={{
                    position: 'absolute',
                    left: [46, 160, 92][index],
                    top: [70, 270, 474][index],
                    opacity: enter,
                    transform: `translateY(${(1 - enter) * 65}px) rotate(${[-6, 3, -2][index]}deg) scale(${0.88 + enter * 0.12})`,
                  }}
                />
              );
            })}
            <PlatformLogo icon={siYoutube} label="YouTube" left={4} top={32} frame={frame} delay={10} fps={fps} />
            <PlatformLogo icon={siBilibili} label="Bilibili" left={620} top={88} frame={frame} delay={24} fps={fps} />
            <PlatformLogo icon={siXiaohongshu} label="小红书" left={12} top={560} frame={frame} delay={38} fps={fps} />
            <PlatformLogo icon={siTiktok} label="TikTok" left={640} top={548} frame={frame} delay={52} fps={fps} />
          </div>
        </div>
      </div>

      <div style={{position: 'absolute', inset: 0, display: 'grid', placeItems: 'center', textAlign: 'center', opacity: promiseOpacity}}>
        <div style={{transform: `translateY(${(1 - logo) * 34}px) scale(${0.9 + logo * 0.1})`}}>
          <div style={{position: 'relative', width: 210, height: 210, margin: '0 auto'}}>
            <div style={{position: 'absolute', inset: -32, border: '2px solid rgba(215,255,40,.32)', borderRadius: 999, transform: `scale(${0.74 + logo * 0.26})`}} />
            <Img src={staticFile('assets/notebook-agent-logo.png')} style={{width: '100%', height: '100%', objectFit: 'contain'}} />
          </div>
          <div style={{marginTop: 38, color: palette.yellow, fontFamily: typography.sans, fontSize: 24, fontWeight: 900, letterSpacing: '.15em'}}>NOTEBOOK AGENT</div>
          <div style={{marginTop: 16, color: palette.white, fontFamily: typography.serif, fontSize: 82, fontWeight: 700, letterSpacing: '-.045em'}}>为你重新找回看过的内容。</div>
          <div style={{marginTop: 28, color: 'rgba(255,253,248,.7)', fontFamily: typography.sans, fontSize: 28}}>把散落的收藏，变成一份可以再次调用的私人知识库。</div>
        </div>
      </div>
    </SceneFrame>
  );
};
