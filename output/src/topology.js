// Downs & Vogel Figure 1. Coordinates describe the display layout in scene units.
export const AREAS = {
  plant:      { code: 'TEP', name: '공정 전체', en: 'Process overview', position: [0, 0, 0], tags: [7, 9, 13, 16, 17, 20] },
  feeds:      { code: 'FD-100', name: '원료 공급', en: 'Feed manifold', position: [-15.2, 0, 0], tags: [1, 2, 3, 4] },
  reactor:    { code: 'R-101', name: '반응기', en: 'Reactor', position: [-7.2, 0, 1.4], tags: [7, 9, 8, 6, 21] },
  condenser:  { code: 'E-101', name: '제품 응축기', en: 'Condenser', position: [-1, 0, -3.4], tags: [22], extra: ['XMEASADD(8)'] },
  separator:  { code: 'V-101', name: '기액 분리기', en: 'Separator', position: [5.4, 0, -2.7], tags: [13, 11, 12, 14] },
  compressor: { code: 'K-101', name: '재순환 압축기', en: 'Compressor', position: [1.8, 0, -7.1], tags: [20, 5] },
  stripper:   { code: 'T-101', name: '제품 스트리퍼', en: 'Stripper', position: [10.1, 0, 2.5], tags: [16, 18, 15, 17] },
  reboiler:   { code: 'E-102', name: '재비기', en: 'Reboiler', position: [13.4, 0, 2.5], tags: [19] },
  purge:      { code: 'PG-109', name: '퍼지 출구', en: 'Purge outlet', position: [15.7, 0, -5.6], tags: [10] },
  product:    { code: 'PR-111', name: '제품 출구', en: 'Product outlet', position: [16.5, 0, 5.2], tags: [17, 40, 41] },
  gc_feed:    { code: 'GC-106', name: '반응기 입구 분석기', en: 'Feed analyzer', position: [-10.9, 0, 3.8], tags: [23, 24, 25, 26, 27, 28] },
  gc_purge:   { code: 'GC-109', name: '퍼지 분석기', en: 'Purge analyzer', position: [12.1, 0, -7.6], tags: [29, 30, 31, 32, 33, 34, 35, 36] },
  gc_product: { code: 'GC-111', name: '제품 분석기', en: 'Product analyzer', position: [14.5, 0, 7.3], tags: [37, 38, 39, 40, 41] },
};

export const MEDIA = {
  gas: { color: '#57d3de', name: '공정 가스 / 원료' },
  liquid: { color: '#e6bb70', name: '액상 / 제품' },
  water: { color: '#649cff', name: '냉각수' },
  steam: { color: '#e6956f', name: '스팀 / 응축수' },
};

const mix = [-10.5, 0.8, 1.4];
const split = [5.4, 5.05, -5.6];
const overheadTie = [-2.7, 3.1, -7.1];
const sump = [10.1, 0.34, 2.5];
const recycleOut = [0.45, 1.13, -7.1];
const recycleIn = [2.98, 1.13, -7.1];
const waterIn = [-11.7, 0.52, 6.65];
const waterOut = [-11.7, 0.52, 7.75];
export const STREAMS = [
  { id: '01', name: 'A 원료', area: 'feeds', media: 'gas', tag: 1, nominal: 0.25, valve: [3, 2], points: [[-13.7,0.78,-3.45],[-10.86,0.78,-3.45],[-10.5,0.8,-3.45],mix] },
  { id: '02', name: 'D 원료', area: 'feeds', media: 'gas', tag: 2, nominal: 3664, valve: [1, 2], points: [[-13.7,0.8,-1.25],[-10.86,0.8,-1.25],[-10.5,0.8,-1.25],mix] },
  { id: '03', name: 'E 원료', area: 'feeds', media: 'gas', tag: 3, nominal: 4509, valve: [2, 2], points: [[-13.7,0.8,0.95],[-10.86,0.8,0.95],[-10.5,0.8,0.95],mix] },
  { id: '04', name: 'A/C 원료 → 스트리퍼', area: 'feeds', media: 'gas', tag: 4, nominal: 9.35, valve: [4, 1.65], points: [[-13.7,0.77,3.15],[-12.1,0.77,3.15],[-12.1,0.77,5.45],[8.16,0.77,5.45],[8.16,2.08,5.45],[8.16,2.08,2.5],[9.06,2.08,2.5]] },
  { id: '05', name: '스트리퍼 상부 환류', area: 'stripper', media: 'gas', pressure: 16, points: [[10.1,7.74,2.5],[10.1,8.17,2.5],[10.1,8.17,-7.1],[-2.7,8.17,-7.1],overheadTie] },
  { id: '06', name: '혼합 원료 → 반응기', area: 'reactor', media: 'gas', tag: 6, pressure: 7, nominal: 42.34, points: [mix,[-9.25,0.8,1.4],[-9.25,1.25,1.4],[-8.64,1.25,1.4]] },
  { id: '07', name: '반응기 배출 → 응축기', area: 'reactor', media: 'gas', pressure: 7, points: [[-7.87,5.64,0.72],[-7.87,6.65,0.72],[-7.87,6.65,-3.4],[-3.2,6.65,-3.4],[-3.2,3.43,-3.4],[-2.97,3.43,-3.4]] },
  { id: '07B', name: '응축기 → 분리기', area: 'condenser', media: 'gas', pressure: 13, points: [[0.96,3.43,-3.4],[2.5,3.43,-3.4],[2.5,2.91,-3.4],[2.5,2.91,-2.7],[4.18,2.91,-2.7]] },
  { id: '08', name: '분리기 가스 → 압축기', area: 'compressor', media: 'gas', pressure: 13, points: [[5.4,5.05,-2.7],split,[3.4,5.05,-5.6],[3.4,5.05,-7.1],[3.4,1.13,-7.1],recycleIn] },
  { id: '08R', name: '압축기 → 반응기 입구', area: 'compressor', media: 'gas', tag: 5, nominal: 26.9, points: [recycleOut,[-0.2,1.13,-7.1],[-0.2,3.1,-7.1],overheadTie,[-10.5,3.1,-7.1],[-10.5,3.1,1.4],mix] },
  { id: '08B', name: '압축기 바이패스', area: 'compressor', media: 'gas', valve: [5, 3.7], points: [[-0.2,1.13,-7.1],[-0.2,2.25,-7.1],[-0.2,2.25,-8.55],[3.4,2.25,-8.55],[3.4,2.25,-7.1]] },
  { id: '09', name: '퍼지 가스', area: 'purge', media: 'gas', tag: 10, pressure: 13, nominal: 0.337, valve: [6, 7.45], points: [split,[7.68,5.05,-5.6],[7.68,1.2,-5.6],[16.95,1.2,-5.6]] },
  { id: '10', name: '분리기 하부 → 스트리퍼', area: 'separator', media: 'liquid', tag: 14, nominal: 25.16, valve: [7, 3.8], points: [[5.4,0.65,-2.7],[5.4,0.65,-1.64],[5.4,0.65,-0.08],[8.4,0.65,-0.08],[8.4,4.72,-0.08],[8.4,4.72,2.5],[9.07,4.72,2.5]] },
  { id: '11', name: '스트리퍼 하부 → 제품', area: 'product', media: 'liquid', tag: 17, nominal: 22.95, valve: [8, 7.75], points: [sump,[10.1,0.34,4.1],[10.1,0.6,5.2],[17.5,0.6,5.2]] },
  { id: '12I', name: '반응기 냉각수 공급', area: 'reactor', media: 'water', tag: 'XMEASADD(6)', nominal: 93.37, points: [waterIn,[-5.73,0.52,6.65],[-5.73,0.52,1.4],[-5.73,3.42,1.4]] },
  { id: '12', name: '반응기 냉각수 회수', area: 'reactor', media: 'water', tag: 'XMEASADD(6)', nominal: 93.37, valve: [10, 5], points: [[-5.73,2.03,1.4],[-5.25,2.03,1.4],[-5.25,2.03,7.75],[-5.25,0.52,7.75],waterOut] },
  { id: '13I', name: '응축기 냉각수 공급', area: 'condenser', media: 'water', tag: 'XMEASADD(8)', nominal: 49.37, points: [[-11.7,0.52,6.65],[-11.7,0.52,-9.3],[-0.12,0.52,-9.3],[-0.12,0.52,-3.95],[-0.12,3.24,-3.95]] },
  { id: '13', name: '응축기 냉각수 회수', area: 'condenser', media: 'water', tag: 'XMEASADD(8)', nominal: 49.37, valve: [11, 3.55], points: [[-1.95,3.22,-3.96],[-1.95,0.42,-3.96],[-1.95,0.42,-10.1],[-12.25,0.42,-10.1],[-12.25,0.42,7.75],waterOut] },
  { id: 'ST', name: '재비기 스팀', area: 'reboiler', media: 'steam', tag: 19, nominal: 230.31, valve: [9, 3.3], points: [[17.3,1.24,6.7],[15.2,1.24,6.7],[15.2,1.24,2.5],[13.78,1.24,2.5]] },
  { id: 'CD', name: '스팀 응축수', area: 'reboiler', media: 'steam', points: [[13.4,0.44,2.9],[13.4,0.44,4.35],[17.3,0.44,4.35]] },
  { id: 'RB-I', name: '컬럼 하부 → 재비기', area: 'reboiler', media: 'liquid', points: [sump,[10.1,0.34,3.66],[13.4,0.34,3.66],[13.4,0.68,2.5]] },
  { id: 'RB-R', name: '재비기 → 컬럼', area: 'reboiler', media: 'gas', points: [[13.4,3.09,2.5],[13.4,3.43,2.5],[11.5,3.43,2.5],[11.5,2.22,2.5],[11.14,2.22,2.5]] },
];

export const codeFor = (tag) => typeof tag === 'number' ? `XMEAS(${tag})` : tag;
