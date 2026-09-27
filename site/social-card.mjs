/** Rasterize the existing geometric wordmark into a portable PNG, using Node only. */
import {deflateSync} from 'node:zlib';

const crcTable = Uint32Array.from({length:256}, (_,i) => {
  let n=i; for(let k=0;k<8;k++)n=n&1?0xedb88320^(n>>>1):n>>>1; return n>>>0;
});
function chunk(type,data){
  const tag=Buffer.from(type), body=Buffer.concat([tag,data]);let crc=0xffffffff;
  for(const byte of body)crc=crcTable[(crc^byte)&255]^(crc>>>8);
  const size=Buffer.alloc(4),check=Buffer.alloc(4);size.writeUInt32BE(data.length);check.writeUInt32BE((crc^0xffffffff)>>>0);
  return Buffer.concat([size,body,check]);
}
function contours(path){
  const tokens=path.match(/[A-Za-z]|-?\d+(?:\.\d+)?/g)||[];
  let i=0,x=0,y=0,current=[],result=[];
  const number=()=>{const n=Number(tokens[i++]);if(!Number.isFinite(n))throw Error('Invalid wordmark coordinate');return n;};
  while(i<tokens.length){
    const command=tokens[i++];
    if(command==='M'){if(current.length)result.push(current);x=number();y=number();current=[[x,y]];}
    else if(command==='L'){x=number();y=number();current.push([x,y]);}
    else if(command==='H'){x=number();current.push([x,y]);}
    else if(command==='V'){y=number();current.push([x,y]);}
    else if(command==='Z'){if(current.length)result.push(current);current=[];}
    else throw Error(`Unsupported wordmark command: ${command}`);
  }
  if(current.length)result.push(current);return result;
}
export function makeSocialCard(wordmark){
  const width=1200,height=630,aa=2,w=width*aa,h=height*aa;
  const pixels=new Uint8Array(w*h); // palette: paper, ink, amber
  function polygon(rings,color){
    const ys=rings.flat().map(p=>p[1]),min=Math.max(0,Math.floor(Math.min(...ys))),max=Math.min(h,Math.ceil(Math.max(...ys)));
    for(let row=min;row<max;row++){
      const scan=row+.5, crossings=[];
      for(const ring of rings)for(let j=0;j<ring.length;j++){
        const [ax,ay]=ring[j],[bx,by]=ring[(j+1)%ring.length];
        if((ay<=scan&&by>scan)||(by<=scan&&ay>scan))crossings.push(ax+(scan-ay)*(bx-ax)/(by-ay));
      }
      crossings.sort((a,b)=>a-b);
      for(let j=0;j+1<crossings.length;j+=2){
        const start=Math.max(0,Math.ceil(crossings[j]-.5)),end=Math.min(w,Math.ceil(crossings[j+1]-.5));
        pixels.fill(color,row*w+start,row*w+end);
      }
    }
  }
  const rect=(x,y,rw,rh,color)=>polygon([[[x*aa,y*aa],[(x+rw)*aa,y*aa],[(x+rw)*aa,(y+rh)*aa],[x*aa,(y+rh)*aa]]],color);
  rect(80,110,1040,1,1);rect(80,520,1040,1,1);rect(1088,57,32,32,2);
  polygon([[[94,55],[106,55],[95,89],[83,89]].map(([x,y])=>[x*aa,y*aa])],1);
  polygon([[[117,55],[129,55],[118,89],[106,89]].map(([x,y])=>[x*aa,y*aa])],1);
  const paths=[...wordmark.matchAll(/<path\b([^>]+)>/g)];
  if(paths.length!==7)throw Error('Expected the seven original DRIFTER letter paths');
  const scale=1040/972;
  for(const [,attrs]of paths){
    const d=attrs.match(/\bd="([^"]+)"/)?.[1];if(!d)throw Error('Missing wordmark path');
    const tx=Number(attrs.match(/translate\(\s*(-?[\d.]+)/)?.[1]||0);
    const rings=contours(d).map(ring=>ring.map(([x,y])=>[(80+(x+tx)*scale)*aa,(220+y*scale)*aa]));
    polygon(rings,1);
  }
  const palette=[[238,237,230],[21,23,19],[255,182,64]];
  const raw=Buffer.alloc(height*(width*3+1));
  for(let y=0;y<height;y++){
    const start=y*(width*3+1);raw[start]=0; // unfiltered RGB scanline
    for(let x=0;x<width;x++)for(let c=0;c<3;c++){
      let sum=0;for(let dy=0;dy<aa;dy++)for(let dx=0;dx<aa;dx++)sum+=palette[pixels[(y*aa+dy)*w+x*aa+dx]][c];
      raw[start+1+x*3+c]=Math.round(sum/(aa*aa));
    }
  }
  const ihdr=Buffer.alloc(13);ihdr.writeUInt32BE(width,0);ihdr.writeUInt32BE(height,4);ihdr[8]=8;ihdr[9]=2;
  return Buffer.concat([Buffer.from([137,80,78,71,13,10,26,10]),chunk('IHDR',ihdr),chunk('IDAT',deflateSync(raw,{level:9})),chunk('IEND',Buffer.alloc(0))]);
}
