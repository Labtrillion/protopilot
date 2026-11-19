// Equipment and stream metadata for the Downs & Vogel Figure 1 layout.
export const AREAS = {
  pump_sep:   { code: 'P-101', name: '분리기 이송 펌프', en: 'Separator transfer pump', tags: [14] },
  pump_prod:  { code: 'P-102', name: '제품 펌프', en: 'Product pump', tags: [17] },
  plant:      { code: 'TEP', name: '공정 전체', en: 'Process overview', tags: [7, 9, 13, 16, 17, 20] },
  feeds:      { code: 'FD-100', name: '원료 공급', en: 'Feed manifold', tags: [1, 2, 3, 4] },
  reactor:    { code: 'R-101', name: '반응기', en: 'Reactor', tags: [7, 9, 8, 6, 21] },
  condenser:  { code: 'E-101', name: '제품 응축기', en: 'Condenser', tags: [22] },
  separator:  { code: 'V-101', name: '기액 분리기', en: 'Separator', tags: [13, 11, 12, 14] },
  compressor: { code: 'K-101', name: '재순환 압축기', en: 'Compressor', tags: [20, 5] },
  stripper:   { code: 'T-101', name: '제품 스트리퍼', en: 'Stripper', tags: [16, 18, 15, 17] },
  reboiler:   { code: 'E-102', name: '재비기', en: 'Reboiler', tags: [19] },
  purge:      { code: 'PG-109', name: '퍼지 출구', en: 'Purge outlet', tags: [10] },
  product:    { code: 'PR-111', name: '제품 출구', en: 'Product outlet', tags: [17, 40, 41] },
  gc_feed:    { code: 'GC-106', name: '반응기 입구 분석기', en: 'Feed analyzer', tags: [23, 24, 25, 26, 27, 28] },
  gc_purge:   { code: 'GC-109', name: '퍼지 분석기', en: 'Purge analyzer', tags: [29, 30, 31, 32, 33, 34, 35, 36] },
  gc_product: { code: 'GC-111', name: '제품 분석기', en: 'Product analyzer', tags: [37, 38, 39, 40, 41] },
};

export const MEDIA = {
  gas: { colorToken: '--process-gas', name: '공정 가스 / 원료' },
  liquid: { colorToken: '--process-liquid', name: '액상 / 제품' },
  water: { colorToken: '--process-water', name: '냉각수' },
  steam: { colorToken: '--process-steam', name: '스팀 / 응축수' },
  mixed: { colorToken: '--process-mixed', name: '응축기 출구 기액' },
};

const PIPES = [
  { id: '01', name: 'A 원료', area: 'feeds', media: 'gas', tag: 1, nominal: 0.25, valve: 3 },
  { id: '02', name: 'D 원료', area: 'feeds', media: 'gas', tag: 2, nominal: 3664, valve: 1 },
  { id: '03', name: 'E 원료', area: 'feeds', media: 'gas', tag: 3, nominal: 4509, valve: 2 },
  { id: 'M', name: 'A/D/E 혼합 헤더', area: 'feeds', media: 'gas' },
  { id: '04', name: 'A/C 원료 → 스트리퍼', area: 'feeds', media: 'gas', tag: 4, nominal: 9.35, valve: 4 },
  { id: '05', name: '스트리퍼 상부 환류', area: 'stripper', media: 'gas' },
  { id: '06', name: '혼합 원료 → 반응기', area: 'reactor', media: 'gas', tag: 6, nominal: 42.34 },
  { id: '07', name: '반응기 배출 → 응축기', area: 'reactor', media: 'gas' },
  { id: '07B', name: '응축기 → 분리기', area: 'condenser', media: 'mixed' },
  { id: '08', name: '분리기 가스 → 압축기', area: 'compressor', media: 'gas' },
  { id: '08I', name: '흡입 티 → 압축기', area: 'compressor', media: 'gas' },
  { id: '08R', name: '압축기 → 반응기 입구', area: 'compressor', media: 'gas', tag: 5, nominal: 26.9 },
  { id: '08M', name: '재순환 혼합 가스', area: 'compressor', media: 'gas' },
  { id: '08B', name: '압축기 바이패스', area: 'compressor', media: 'gas', valve: 5 },
  { id: '09', name: '퍼지 가스', area: 'purge', media: 'gas', tag: 10, nominal: 0.337, valve: 6 },
  { id: '10S', name: '분리기 하부 → 펌프', area: 'separator', media: 'liquid', tag: 14, nominal: 25.16 },
  { id: '10', name: '이송 펌프 → 스트리퍼', area: 'separator', media: 'liquid', tag: 14, nominal: 25.16, valve: 7 },
  { id: '11S', name: '스트리퍼 하부 → 펌프', area: 'product', media: 'liquid', tag: 17, nominal: 22.95 },
  { id: '11', name: '제품 펌프 → 제품 출구', area: 'product', media: 'liquid', tag: 17, nominal: 22.95, valve: 8 },
  { id: 'CWS', name: '공용 냉각수 공급', area: 'plant', media: 'water' },
  { id: 'CWR', name: '공용 냉각수 회수', area: 'plant', media: 'water' },
  { id: '12I', name: '반응기 냉각수 공급', area: 'reactor', media: 'water', tag: 'XMEASADD(6)', nominal: 93.37 },
  { id: '12', name: '반응기 냉각수 회수', area: 'reactor', media: 'water', tag: 'XMEASADD(6)', nominal: 93.37, valve: 10 },
  { id: '13I', name: '응축기 냉각수 공급', area: 'condenser', media: 'water', tag: 'XMEASADD(8)', nominal: 49.37 },
  { id: '13', name: '응축기 냉각수 회수', area: 'condenser', media: 'water', tag: 'XMEASADD(8)', nominal: 49.37, valve: 11 },
  { id: 'ST', name: '재비기 스팀', area: 'reboiler', media: 'steam', tag: 19, nominal: 230.31, valve: 9 },
  { id: 'CD', name: '스팀 응축수', area: 'reboiler', media: 'steam' },
  { id: 'RB-I', name: '컬럼 하부 → 재비기', area: 'reboiler', media: 'liquid' },
  { id: 'RB-R', name: '재비기 → 컬럼', area: 'reboiler', media: 'gas' },
];

export const codeFor = (tag) => typeof tag === 'number' ? `XMEAS(${tag})` : tag;

const coolingSignals = {
  reactor: new Set(['XMEAS(9)', 'XMEAS(21)', 'XMEASADD(5)', 'XMEASADD(6)']),
  condenser: new Set(['XMEAS(22)', 'XMEASADD(7)', 'XMEASADD(8)']),
};

export function hasCoolingPath({ area, tag, source }) {
  return source !== '센서 시험' && Boolean(coolingSignals[area]?.has(tag));
}

export function streamMatchesTag(stream, tag) {
  return Boolean(tag && (codeFor(stream.tag) === tag || (stream.valve && `XMV(${stream.valve})` === tag)));
}


export const CORE_PRESSURES = {
  reactor: 7, separator: 13, stripper: 16,
};

export const LEVEL_TAGS = { reactor: 8, separator: 12, stripper: 15 };

export function levelReading(status, area) {
  const tag = codeFor(LEVEL_TAGS[area]);
  return status?.native_values?.[tag] ?? status?.values?.[tag];
}

export function levelFraction(percent) {
  return Number.isFinite(percent) ? Math.max(0, Math.min(100, percent)) / 100 : 0;
}

// Instruments mounted on interior parts of the 3D vessels: [tag, part name].
export const INTERIOR_PARTS = {
  reactor: [['XMEAS(8)', '액면'], ['XMV(12)', '교반기'], ['XMEAS(21)', '냉각 코일']],
  separator: [['XMEAS(12)', '액면']],
  stripper: [['XMEAS(15)', '액면']],
};
export const INTERIOR_TAGS = new Set(Object.values(INTERIOR_PARTS).flat().map(([tag]) => tag));

export const CIRCUITS = {
  recycle: {
    title: '재순환', paths: [
      ['기액 분리기', 'K-101 압축기', '혼합 헤더', 'R-101 반응기'],
      ['T-101 상부', '압축기 토출측 합류', '혼합 헤더'],
      ['압축기 토출', 'XMV(5) 바이패스', '압축기 흡입'],
    ],
    info: '분리기 상부에서 퍼지 #09가 분기됩니다. 스트리퍼 상부 #05는 압축기 토출 유량계 뒤에서 합류합니다.',
  },
  cooling: {
    title: '냉각수', paths: [
      ['CWS 공급', '반응기 코일', 'XMV(10)', 'CWR 회수'],
      ['CWS 공급', '응축기 튜브', 'XMV(11)', 'CWR 회수'],
    ],
    info: '두 설비는 공용 냉각수 헤더에 병렬로 연결됩니다. 각 회수 배관에 제어 밸브가 있습니다.',
  },
  steam: {
    title: '스팀·재비기', paths: [
      ['STM 공급', 'XMV(9)', 'E-102 재비기', 'COND 회수'],
      ['T-101 하부', 'E-102 공정측', 'T-101 하부 증기 환류'],
    ],
    info: '재비기에서 스팀과 스트리퍼 공정 유체는 별도 유로를 지나며 열을 교환합니다.',
  },
};

// [source label, destination label, connected equipment, optional circuit]
const LINKS = {
  '01': ['A 원료', '혼합 헤더', ['feeds']],
  '02': ['D 원료', '혼합 헤더', ['feeds']],
  '03': ['E 원료', '혼합 헤더', ['feeds']],
  'M': ['A/D/E 합류', '재순환 합류점', ['feeds']],
  '04': ['A/C 원료', 'T-101 하부', ['feeds', 'stripper']],
  '05': ['T-101 상부', '압축기 토출측 혼합점', ['stripper'], 'recycle'],
  '06': ['혼합 헤더', 'R-101 입구', ['feeds', 'reactor'], 'recycle'],
  '07': ['R-101 상부', 'E-101 공정측 입구', ['reactor', 'condenser']],
  '07B': ['E-101 공정측 출구', 'V-101 측면', ['condenser', 'separator']],
  '08': ['V-101 상부', '압축기 흡입 티', ['separator', 'compressor'], 'recycle'],
  '08I': ['흡입 티', 'K-101 흡입', ['compressor'], 'recycle'],
  '08R': ['K-101 토출', '#05 합류점', ['compressor'], 'recycle'],
  '08M': ['#05 합류점', '원료 혼합 헤더', ['feeds'], 'recycle'],
  '08B': ['K-101 토출 티', 'K-101 흡입 티', ['compressor'], 'recycle'],
  '09': ['V-101 상부 분기', 'PURGE 출구', ['separator', 'purge']],
  '10S': ['V-101 하부', 'P-101 흡입', ['separator', 'pump_sep']],
  '10': ['P-101 토출', 'T-101 상부 측면', ['pump_sep', 'stripper']],
  '11S': ['T-101 하부', 'P-102 흡입', ['stripper', 'pump_prod']],
  '11': ['P-102 토출', 'PRODUCT 출구', ['pump_prod', 'product']],
  'CWS': ['CWS 배터리 리밋', '냉각수 공급 헤더', [], 'cooling'],
  'CWR': ['냉각수 회수 헤더', 'CWR 배터리 리밋', [], 'cooling'],
  '12I': ['CWS 공급 헤더', 'R-101 코일 입구', ['reactor'], 'cooling'],
  '12': ['R-101 코일 출구', 'CWR 회수 헤더', ['reactor'], 'cooling'],
  '13I': ['CWS 공급 헤더', 'E-101 냉각수 입구', ['condenser'], 'cooling'],
  '13': ['E-101 냉각수 출구', 'CWR 회수 헤더', ['condenser'], 'cooling'],
  'ST': ['STM 경계', 'E-102 스팀측', ['reboiler'], 'steam'],
  'CD': ['E-102 스팀측', 'COND 경계', ['reboiler'], 'steam'],
  'RB-I': ['T-101 하부 분기', 'E-102 공정측 입구', ['stripper', 'reboiler'], 'steam'],
  'RB-R': ['E-102 공정측 출구', 'T-101 하부', ['stripper', 'reboiler'], 'steam'],
};

export const STREAMS = PIPES.map((pipe) => {
  const [from, to, connectedAreas, circuit] = LINKS[pipe.id];
  return { ...pipe, from, to, connectedAreas, circuit };
});

export const FEED_TAGS = Object.fromEntries(STREAMS.filter(({ area, tag }) => area === 'feeds' && tag)
  .map(({ id, tag }) => [id, codeFor(tag)]));

