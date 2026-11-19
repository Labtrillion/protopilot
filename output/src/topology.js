// Downs & Vogel Figure 1. Coordinates describe the display layout in scene units.
export const AREAS = {
  pump_sep:   { code: 'P-101', name: '분리기 이송 펌프', en: 'Separator transfer pump', position: [5.4, 0, -0.77], tags: [14] },
  pump_prod:  { code: 'P-102', name: '제품 펌프', en: 'Product pump', position: [10.1, 0, 4.36], tags: [17] },
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
  water: { color: '#71adff', name: '냉각수' },
  steam: { color: '#eda477', name: '스팀 / 응축수' },
  mixed: { color: '#a0d9c1', name: '응축기 출구 기액' },
};

const mix = [-10.5, 0.8, 1.4];
const split = [5.4, 5.05, -5.6];
const overheadTie = [-2.7, 3.1, -7.1];
const sump = [10.1, 0.34, 2.5];
const inletTee = [3.4, 2.25, -7.1];
const dischargeTee = [-0.2, 1.13, -7.1];
const sumpTee = [10.1, 0.34, 3.39];
const recycleOut = [0.45, 1.13, -7.1];
const recycleIn = [2.98, 1.13, -7.1];
const waterIn = [-11.7, 0.52, 6.65];
const waterOut = [-11.7, 0.52, 7.75];
const PIPES = [
  { id: '01', name: 'A 원료', area: 'feeds', media: 'gas', tag: 1, nominal: 0.25, valve: [3, 2], points: [[-13.7,0.78,-3.45],[-10.86,0.78,-3.45],[-10.5,0.8,-3.45]] },
  { id: '02', name: 'D 원료', area: 'feeds', media: 'gas', tag: 2, nominal: 3664, valve: [1, 2], points: [[-13.7,0.8,-1.25],[-10.86,0.8,-1.25],[-10.5,0.8,-1.25]] },
  { id: '03', name: 'E 원료', area: 'feeds', media: 'gas', tag: 3, nominal: 4509, valve: [2, 2], points: [[-13.7,0.8,0.95],[-10.86,0.8,0.95],[-10.5,0.8,0.95]] },
  { id: 'M', name: 'A/D/E 혼합 헤더', area: 'feeds', media: 'gas', points: [[-10.5,0.8,-3.45],[-10.5,0.8,1.4]] },
  { id: '04', name: 'A/C 원료 → 스트리퍼', area: 'feeds', media: 'gas', tag: 4, nominal: 9.35, valve: [4, 1.65], points: [[-13.7,0.77,3.15],[-12.1,0.77,3.15],[-12.1,0.77,5.45],[8.16,0.77,5.45],[8.16,2.08,5.45],[8.16,2.08,2.5],[9.06,2.08,2.5]] },
  { id: '05', name: '스트리퍼 상부 환류', area: 'stripper', media: 'gas', points: [[10.1,7.74,2.5],[10.1,8.17,2.5],[10.1,8.17,-7.1],[-2.7,8.17,-7.1],overheadTie] },
  { id: '06', name: '혼합 원료 → 반응기', area: 'reactor', media: 'gas', tag: 6, nominal: 42.34, points: [mix,[-9.25,0.8,1.4],[-9.25,1.25,1.4],[-8.64,1.25,1.4]] },
  { id: '07', name: '반응기 배출 → 응축기', area: 'reactor', media: 'gas', points: [[-7.87,5.64,0.72],[-7.87,6.65,0.72],[-7.87,6.65,-3.4],[-3.2,6.65,-3.4],[-3.2,3.43,-3.4],[-2.97,3.43,-3.4]] },
  { id: '07B', name: '응축기 → 분리기', area: 'condenser', media: 'mixed', points: [[0.96,3.43,-3.4],[2.5,3.43,-3.4],[2.5,2.91,-3.4],[2.5,2.91,-2.7],[4.18,2.91,-2.7]] },
  { id: '08', name: '분리기 가스 → 압축기', area: 'compressor', media: 'gas', points: [[5.4,5.05,-2.7],split,[3.4,5.05,-5.6],[3.4,5.05,-7.1],inletTee] },
  { id: '08I', name: '흡입 티 → 압축기', area: 'compressor', media: 'gas', points: [inletTee,[3.4,1.13,-7.1],recycleIn] },
  { id: '08R', name: '압축기 → 반응기 입구', area: 'compressor', media: 'gas', tag: 5, nominal: 26.9, points: [recycleOut,dischargeTee,[-0.2,3.1,-7.1],overheadTie] },
  { id: '08M', name: '재순환 혼합 가스', area: 'compressor', media: 'gas', points: [overheadTie,[-10.5,3.1,-7.1],[-10.5,3.1,1.4],mix] },
  { id: '08B', name: '압축기 바이패스', area: 'compressor', media: 'gas', valve: [5, 3.7], points: [dischargeTee,[-0.2,2.25,-7.1],[-0.2,2.25,-8.55],[3.4,2.25,-8.55],inletTee] },
  { id: '09', name: '퍼지 가스', area: 'purge', media: 'gas', tag: 10, nominal: 0.337, valve: [6, 7.45], points: [split,[7.68,5.05,-5.6],[7.68,1.2,-5.6],[16.95,1.2,-5.6]] },
  { id: '10S', name: '분리기 하부 → 펌프', area: 'separator', media: 'liquid', tag: 14, nominal: 25.16, points: [[5.4,0.44,-2.7],[5.4,0.44,-2.13],[5.4,0.70,-2.13],[5.4,0.70,-1.53]] },
  { id: '10', name: '이송 펌프 → 스트리퍼', area: 'separator', media: 'liquid', tag: 14, nominal: 25.16, valve: [7, 1.82], points: [[5.4,0.70,0.06],[8.4,0.70,0.06],[8.4,4.72,0.06],[8.4,4.72,2.5],[9.07,4.72,2.5]] },
  { id: '11S', name: '스트리퍼 하부 → 펌프', area: 'product', media: 'liquid', tag: 17, nominal: 22.95, points: [sump,sumpTee,[10.1,0.70,3.6]] },
  { id: '11', name: '제품 펌프 → 제품 출구', area: 'product', media: 'liquid', tag: 17, nominal: 22.95, valve: [8, 5.44], points: [[10.1,0.70,5.19],[17.5,0.70,5.19]] },
  { id: 'CWS', name: '공용 냉각수 공급', area: 'plant', media: 'water', points: [[-13.4,0.52,6.65],waterIn] },
  { id: 'CWR', name: '공용 냉각수 회수', area: 'plant', media: 'water', points: [waterOut,[-13.4,0.52,7.75]] },
  { id: '12I', name: '반응기 냉각수 공급', area: 'reactor', media: 'water', tag: 'XMEASADD(6)', nominal: 93.37, points: [waterIn,[-5.73,0.52,6.65],[-5.73,0.52,1.4],[-5.73,3.42,1.4]] },
  { id: '12', name: '반응기 냉각수 회수', area: 'reactor', media: 'water', tag: 'XMEASADD(6)', nominal: 93.37, valve: [10, 5], points: [[-5.73,2.03,1.4],[-5.25,2.03,1.4],[-5.25,2.03,7.75],[-5.25,0.52,7.75],waterOut] },
  { id: '13I', name: '응축기 냉각수 공급', area: 'condenser', media: 'water', tag: 'XMEASADD(8)', nominal: 49.37, points: [[-11.7,0.52,6.65],[-11.7,0.52,-9.3],[-0.12,0.52,-9.3],[-0.12,0.52,-3.95],[-0.12,3.24,-3.95]] },
  { id: '13', name: '응축기 냉각수 회수', area: 'condenser', media: 'water', tag: 'XMEASADD(8)', nominal: 49.37, valve: [11, 3.55], points: [[-1.95,3.22,-3.96],[-1.95,0.42,-3.96],[-1.95,0.42,-10.1],[-12.25,0.42,-10.1],[-12.25,0.42,7.75],waterOut] },
  { id: 'ST', name: '재비기 스팀', area: 'reboiler', media: 'steam', tag: 19, nominal: 230.31, valve: [9, 3.6], points: [[17.3,1.24,6.7],[15.2,1.24,6.7],[15.2,1.24,2.5],[13.78,1.24,2.5]] },
  { id: 'CD', name: '스팀 응축수', area: 'reboiler', media: 'steam', points: [[13.67,0.83,2.77],[13.67,0.83,4.35],[17.3,0.83,4.35]] },
  { id: 'RB-I', name: '컬럼 하부 → 재비기', area: 'reboiler', media: 'liquid', points: [sumpTee,[12.78,0.34,3.39],[12.78,0.34,2.5],[13.4,0.44,2.5]] },
  { id: 'RB-R', name: '재비기 → 컬럼', area: 'reboiler', media: 'gas', points: [[13.4,3.09,2.5],[13.4,3.43,2.5],[11.5,3.43,2.5],[11.5,2.22,2.5],[11.14,2.22,2.5]] },
];

export const codeFor = (tag) => typeof tag === 'number' ? `XMEAS(${tag})` : tag;

export function streamMatchesTag(stream, tag) {
  return Boolean(tag && (codeFor(stream.tag) === tag || (stream.valve && `XMV(${stream.valve[0]})` === tag)));
}


export const CORE_PRESSURES = {
  reactor: 7, separator: 13, stripper: 16,
};

export const CIRCUITS = {
  recycle: {
    title: '재순환', color: '#65e1db', paths: [
      ['기액 분리기', 'K-101 압축기', '혼합 헤더', 'R-101 반응기'],
      ['T-101 상부', '압축기 토출측 합류', '혼합 헤더'],
      ['압축기 토출', 'XMV(5) 바이패스', '압축기 흡입'],
    ],
    info: '분리기 상부에서 퍼지 #09가 분기됩니다. 스트리퍼 상부 #05는 압축기 토출 유량계 뒤에서 합류합니다.',
  },
  cooling: {
    title: '냉각수', color: '#89baff', paths: [
      ['CWS 공급', '반응기 코일', 'XMV(10)', 'CWR 회수'],
      ['CWS 공급', '응축기 튜브', 'XMV(11)', 'CWR 회수'],
    ],
    info: '두 설비는 공용 냉각수 헤더에 병렬로 연결됩니다. 각 회수 배관에 제어 밸브가 있습니다.',
  },
  steam: {
    title: '스팀·재비기', color: '#ffb385', paths: [
      ['STM 공급', 'XMV(9)', 'E-102 재비기', 'COND 회수'],
      ['T-101 하부', 'E-102 공정측', 'T-101 하부 증기 환류'],
    ],
    info: '재비기에서 스팀과 스트리퍼 공정 유체는 별도 유로를 지나며 열을 교환합니다.',
  },
};

const LINKS = {
  '01': ['A 원료', '혼합 헤더'], '02': ['D 원료', '혼합 헤더'], '03': ['E 원료', '혼합 헤더'],
  'M': ['A/D/E 합류', '재순환 합류점'],
  '04': ['A/C 원료', 'T-101 하부'],
  '05': ['T-101 상부', '압축기 토출측 혼합점', 'recycle'],
  '06': ['혼합 헤더', 'R-101 입구', 'recycle'],
  '07': ['R-101 상부', 'E-101 공정측 입구'],
  '07B': ['E-101 공정측 출구', 'V-101 측면'],
  '08': ['V-101 상부', '압축기 흡입 티', 'recycle'],
  '08I': ['흡입 티', 'K-101 흡입', 'recycle'],
  '08R': ['K-101 토출', '#05 합류점', 'recycle'],
  '08M': ['#05 합류점', '원료 혼합 헤더', 'recycle'],
  '08B': ['K-101 토출 티', 'K-101 흡입 티', 'recycle'],
  '09': ['V-101 상부 분기', 'PURGE 출구'],
  '10S': ['V-101 하부', 'P-101 흡입'],
  '10': ['P-101 토출', 'T-101 상부 측면'],
  '11S': ['T-101 하부', 'P-102 흡입'],
  '11': ['P-102 토출', 'PRODUCT 출구'],
  'CWS': ['CWS 배터리 리밋', '냉각수 공급 헤더', 'cooling'],
  'CWR': ['냉각수 회수 헤더', 'CWR 배터리 리밋', 'cooling'],
  '12I': ['CWS 공급 헤더', 'R-101 코일 입구', 'cooling'],
  '12': ['R-101 코일 출구', 'CWR 회수 헤더', 'cooling'],
  '13I': ['CWS 공급 헤더', 'E-101 냉각수 입구', 'cooling'],
  '13': ['E-101 냉각수 출구', 'CWR 회수 헤더', 'cooling'],
  'ST': ['STM 경계', 'E-102 스팀측', 'steam'],
  'CD': ['E-102 스팀측', 'COND 경계', 'steam'],
  'RB-I': ['T-101 하부 분기', 'E-102 공정측 입구', 'steam'],
  'RB-R': ['E-102 공정측 출구', 'T-101 하부', 'steam'],
};

export const STREAMS = PIPES.map((pipe) => {
  const [from, to, circuit] = LINKS[pipe.id];
  return { ...pipe, from, to, circuit };
});

export const PORTS = [
  ['A', [-13.7,1.58,-3.45], 'gas', ''], ['D', [-13.7,1.58,-1.25], 'gas', ''],
  ['E', [-13.7,1.58,0.95], 'gas', ''], ['A/C', [-13.7,1.58,3.15], 'gas', ''],
  ['CWS ↓ 공급', [-13.4,0.52,6.65], 'water', 'cooling'],
  ['CWR ↑ 회수', [-13.4,0.52,7.75], 'water', 'cooling'],
  ['STM 공급', [17.3,1.24,6.7], 'steam', 'steam'],
  ['COND 회수', [17.3,0.83,4.35], 'steam', 'steam'],
];
