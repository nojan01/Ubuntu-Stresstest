import subprocess,json,hashlib
from pathlib import Path
root=Path(__file__).resolve().parents[1]/'assets/video-codec'
meta={}
for codec,encoder,options in [('h264','libx264',['-preset','fast']),('hevc','libx265',['-preset','fast','-x265-params','pools=2:frame-threads=2']),('av1','libaom-av1',['-cpu-used','8','-row-mt','1'])]:
 p=root/(codec+'.mkv')
 subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-f','lavfi','-i','testsrc2=size=1280x720:rate=30','-frames:v','30','-an','-c:v',encoder,'-threads','2',*options,'-pix_fmt','yuv420p',str(p)],check=True)
 meta[codec]={'file':p.name,'frames':30,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
(root/'manifest.json').write_text(json.dumps(meta,indent=2)+'\n')
