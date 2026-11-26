#!/usr/bin/env python3
"""Build a *review-only* two-resolution Reineke map-art draft.

The input must be an independently painted region concept, not the original
LOD1 terrain. The output is georegistered using hand-reviewed image-space
anchors, then a second *newly detailed* local rendition is painted on top.

This script makes no assertion that unverified source building footprints or
roads are geographically authoritative. Neither output is production-approved.
"""
from __future__ import annotations

import argparse
import json
import math
import random
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from scipy.interpolate import RBFInterpolator

WORLD = (1600, 900)
REGION_BOUNDS = (320, 240, 960, 540)
LOCAL_BOUNDS = (480, 330, 480, 240)
REGION_SIZE = (1920, 1080)
LOCAL_SIZE = (1920, 960)

# Manual visual assessment of concept image, mapped to 1920x1080 pixels.
# These are guessed roof/well centres, NOT externally verified building geometry.
SOURCE_ANCHORS = {
    "houses.origin": (610, 244),
    "home": (711, 466),
    "workshop": (815, 584),
    "kitchen": (854, 464),
    "well": (976, 509),
    "storage": (1072, 470),
    "shop": (1080, 593),
    "settlement": (969, 356),
}

# Image-space mapping in static/map/coords.json, not the separate 'locations'
# coordinates; must eventually validate against the rendering transform.
IMAGE_SPACE = {
    "houses.origin": (39.90, 42.20),
    "home": (42.54, 51.40),
    "workshop": (45.18, 56.00),
    "kitchen": (47.83, 51.40),
    "well": (50.47, 51.40),
    "storage": (53.11, 51.40),
    "shop": (53.11, 56.00),
    "settlement": (50.47, 46.80),
}

def desired_anchors() -> dict[str, tuple[float, float]]:
    return {name: ((wx / 100 * WORLD[0] - REGION_BOUNDS[0]) * REGION_SIZE[0] / REGION_BOUNDS[2],
                   (wy / 100 * WORLD[1] - REGION_BOUNDS[1]) * REGION_SIZE[1] / REGION_BOUNDS[3])
            for name, (wx, wy) in IMAGE_SPACE.items()}

def register_image(original: Image.Image) -> Image.Image:
    """Smooth local displacement from concept POI centres to image-space POI.

    Hold the outer perimeter fixed so the ROI does not lose its bounding box.
    This does not prove authorial geometry is correct; it merely anchors POIs.
    """
    source = np.array(original.convert("RGB"))
    w, h = REGION_SIZE
    target = desired_anchors()
    fixed = [
      (0, 0), (w/4,0),(w/2,0),(w*3/4,0),(w,0),
      (0,h/2),(w,h/2), (0,h),(w/4,h),(w/2,h),(w*3/4,h),(w,h),
      (0,h/4),(w,h/4),(0,h*3/4),(w,h*3/4)
    ]
    # Displacement is inverse: target pixel -> original source pixel.
    points = np.array([target[k] for k in SOURCE_ANCHORS] + fixed, dtype=np.float64)
    delta = np.array([(SOURCE_ANCHORS[k][0] - target[k][0], SOURCE_ANCHORS[k][1] - target[k][1])
                      for k in SOURCE_ANCHORS] + [(0.0,0.0)]*len(fixed), dtype=np.float64)
    interpolator = RBFInterpolator(points, delta, kernel="thin_plate_spline", smoothing=65.0)
    xs = np.linspace(0,w-1,65)
    ys = np.linspace(0,h-1,38)
    xx, yy = np.meshgrid(xs,ys)
    coarse = interpolator(np.stack([xx.ravel(),yy.ravel()],axis=-1)).reshape(
        len(ys),len(xs),2).astype('float32')
    shift_x=cv2.resize(coarse[:,:,0], (w,h),interpolation=cv2.INTER_CUBIC)
    shift_y=cv2.resize(coarse[:,:,1], (w,h),interpolation=cv2.INTER_CUBIC)
    grid_x, grid_y=np.meshgrid(np.arange(w,dtype=np.float32),np.arange(h,dtype=np.float32))
    warped = cv2.remap(source, grid_x+shift_x, grid_y+shift_y,
                       interpolation=cv2.INTER_LANCZOS4, borderMode=cv2.BORDER_REFLECT_101)
    return Image.fromarray(warped)


def faded_mask(w:int,h:int,fade:int=130)->np.ndarray:
    """Full opacity in the interior, tapered to zero on all four edges."""
    dx=np.minimum(np.arange(w), w-1-np.arange(w)).astype('float32')
    dy=np.minimum(np.arange(h), h-1-np.arange(h)).astype('float32')
    edge=np.minimum(dy[:,None],dx[None,:])/fade
    return np.clip(edge,0,1)**1.5


def local_detail(region:Image.Image, seed:int=308)->Image.Image:
    """Add high-frequency, native LOD3 environmental/roof detail (concept art).

    The LOD3 base is cropped exactly from LOD2 in canonical world space; new
    texture and construction detail is authored only at 1920px, not a copy of
    the same pixels. No new roads, people, or building footprints are drawn.
    """
    rx, ry, rw, rh = REGION_BOUNDS
    lx, ly, lw, lh = LOCAL_BOUNDS
    px = REGION_SIZE[0]/rw
    crop = (int((lx-rx)*px), int((ly-ry)*px),int((lx+lw-rx)*px),int((ly+lh-ry)*px))
    image = region.crop(crop).resize(LOCAL_SIZE, Image.Resampling.LANCZOS)
    image = image.filter(ImageFilter.UnsharpMask(radius=1.6,percent=125,threshold=3))
    arr = np.array(image.convert('RGB'), dtype=np.float32)
    h,w = arr.shape[:2]
    rng = np.random.default_rng(seed)
    alpha = faded_mask(w,h,fade=120)
    # Fine grain on ground/foliage; lower strength over rooftops.
    # Pixel colors green-dominant indicate vegetation; gray/brown are structures.
    vegetation = (arr[:,:,1] > arr[:,:,0]*0.90) & (arr[:,:,1] > arr[:,:,2]*1.04)
    grain = rng.normal(0,5.1,size=(h,w)).astype('float32')
    grain = cv2.GaussianBlur(grain,(3,3),0.52)
    local_strength = np.where(vegetation,1.0,0.38)*alpha
    arr += grain[:,:,None]*local_strength[:,:,None]
    arr = np.clip(arr,0,255).astype('uint8')
    image = Image.fromarray(arr)
    draw = ImageDraw.Draw(image,'RGBA')
    py = random.Random(seed)
    # New stones, grass clumps, foliage texture. Small enough not to imply
    # roads / buildings / interactable gameplay objects.
    for _ in range(9200):
        x=py.randrange(55,w-55)
        y=py.randrange(55,h-55)
        if alpha[y,x] < 0.15:
            continue
        rr,gg,bb=arr[y,x]
        if gg < rr*0.83 or gg < bb*0.95:
            continue
        r=py.choice([1,1,2,2,3])
        color=py.choice([(174,172,119,80),(112,132,75,92),(209,194,142,68),(68,100,53,90)])
        draw.ellipse((x-r,y-r,x+r,y+r), fill=color)
        if py.random()<0.28:
            draw.line((x,y-2,x+py.randrange(-3,4),y-7),fill=(101,124,61,90),width=1)
    # Additional roof and wall microdetail, manually placed on reviewed
    # rooftops in the registered LOD2 draft. The coordinates are normalized
    # to the local crop and intentionally do not add new footprints.
    # (centre_x, centre_y, width, height, orientation degrees)
    # Geometry constrained to existing roof regions from visual inspection.
    roofs=[
      (315,115,184,62,-12),  # western 1
      (485,64,105,57,-24),   # western 2
      (1290,275,268,93,0),  # settlement / communal building
      (753,532,160,59,-20), # home
      (1100,553,160,56,-9), # kitchen
      (1660,549,185,58,-2), # storage
      (967,811,232,66,-8), # workshop
      (1682,835,204,67,-3),# shop
    ]
    for cx,cy,rw,rh,theta in roofs:
        rad=math.radians(theta)
        vx,vy=math.cos(rad),math.sin(rad)
        # Directional planks / slate seams to show new detail only at LOD3.
        for v in np.arange(-rh/2+7,rh/2-2,8):
            start=(cx-rw/2*vx-v*(-vy),cy-rw/2*vy-v*vx)
            end=(cx+rw/2*vx-v*(-vy),cy+rw/2*vy-v*vx)
            # Restrict to known approximate roof polygon; subtle contrast.
            draw.line((*start,*end),fill=(39,34,26,47),width=1)
        for _ in range(max(4,int(rw/19))):
            along=py.uniform(-rw/2+9,rw/2-9)
            cross=py.uniform(-rh/2+6,rh/2-6)
            x=cx+along*vx-cross*vy
            y=cy+along*vy+cross*vx
            draw.line((x,y,x+py.uniform(3,12),y+1),fill=(250,225,164,39),width=1)
    # Distinct high-res local details: sparsely placed tool / wood grain
    # texture inside clearing, avoiding speculative structures and NPC.
    for _ in range(900):
        x=py.randint(400,w-420)
        y=py.randint(380,h-145)
        if alpha[y,x] < 0.7:
            continue
        rr,gg,bb=arr[y,x]
        if abs(int(rr)-int(gg)) > 55:
            continue
        r=py.choice([1,2,2,3])
        color=py.choice([(60,52,38,60),(200,181,145,65)])
        draw.ellipse((x-r,y-r//2,x+r,y+r),fill=color)
    return image


def make(source:Path,output:Path,registration_report:Path|None=None)->dict:
    output.mkdir(parents=True,exist_ok=True)
    original=Image.open(source).convert('RGB').resize(REGION_SIZE,Image.Resampling.LANCZOS)
    region=register_image(original)
    region_path=output/'lod2-region.png'
    region.save(region_path,optimize=True)
    local=local_detail(region)
    local_path=output/'lod3-settlement.png'
    local.save(local_path,optimize=True)
    checkpoints=desired_anchors()
    report={
      'status':'unapproved_visual_prototype',
      'worldSize':WORLD,
      'lod2':{'source':str(region_path.name),'bounds':REGION_BOUNDS,'size':REGION_SIZE},
      'lod3':{'source':str(local_path.name),'bounds':LOCAL_BOUNDS,'size':LOCAL_SIZE},
      'sourceAnchors':SOURCE_ANCHORS,
      'targetAnchors':{k:[round(v[0],2),round(v[1],2)] for k,v in checkpoints.items()},
      'targetCoordinateSet':'image_space in static/map/coords.json',
      'warnings':[
        'Generated concept includes inferred architecture and paths; not geographically verified.',
        'Image registration uses hand-picked approximate roof centres, '
        'not validated building footprints.',
        'LOD3 adds independent rendered micro-detail, but it is derived from '
        'LOD2 terrain for continuity.',
        'Artwork requires design approval and actual overlay QA with the 1600x900 base raster.'
      ]
    }
    (output/'registration.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    return report


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--input',type=Path,required=True)
    ap.add_argument('--output',type=Path,required=True)
    args=ap.parse_args()
    r=make(args.input,args.output)
    print('Created independent LOD2 concept and detailed LOD3 derivative in',args.output)
    print('Checkpoints:',len(r['targetAnchors']),'review status:',r['status'])

if __name__=='__main__':
    main()
