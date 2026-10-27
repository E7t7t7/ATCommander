/* Isolated synthetic public-ABI heap. No browser, file, account or network data. */
const fs = require('fs');
(async () => {
  const input = JSON.parse(fs.readFileSync(0, 'utf8'));
  const scenario = input.scenario || {};
  const memory = new ArrayBuffer(2 * 1024 * 1024);
  const h = new Uint32Array(memory), b = new Uint8Array(memory), f = new Float32Array(memory);
  const calls = [], allocations = [], frees = [], active = new Set(), allowedChanges = new Set();
  const write = (address, value) => { h[address >>> 2] = value; };
  const text = (address, value) => { b.set(Buffer.from(value + '\0'), address); };
  const base = 4096;
  if (!scenario.metadata_missing) { write(base, 0xFAB11BAF); write(base + 4, 39); write(base + 8, 380); }
  const classes = {};
  // Independent golden declarations from public metadata; a wrong production
  // backing-field name/count must fail against these, not rewrite the fixture.
  const current = scenario.build === 'current';
  const abi = current ? {
    game_object: 3158, active: 6518, transform: 5429, screen_to_local: 38652,
    move_card: 34812, start_coroutine: 5957, drag_begin: 29952, drag_move: 29953,
    drag_end: 29954, update: 29956, late_update: 29957,
  } : {
    game_object: 2825, active: 5538, transform: 4346, screen_to_local: 37702,
    move_card: 33882, start_coroutine: 4514, drag_begin: 29040, drag_move: 29041,
    drag_end: 29042, update: 29044, late_update: 29045,
  };
  const golden = {
    ResearchPopup: {offset: 764598, count: 42, fields: {
      _tabs: [3, 112], _library: [6, 124], _card1: [14, 156], _card2: [21, 184],
      '<Index>k__BackingField': [38, 252], _isAdLocked: [41, 258]}},
    CardView: {offset: 775107, count: 107, fields: {
      '<CardID>k__BackingField': [56, 260], '<CardData>k__BackingField': [98, 388]}},
    CardScrollerViewNew: {offset: 785084, count: 60, fields: {
      EnableDragging: [19, 42], _cardViews: [30, 84], _cardData: [31, 88],
      _cardStacks: [33, 96], _containerWidth: [36, 108], _cardWidth: [39, 120],
      _scrollX: [41, 128], _maxScrollX: [42, 132],
      _needsReload: [55, 185], _needsSortFilter: [56, 186]}},
    ServiceCardData: {offset: 617089, count: 17, fields: {
      '<CardID>k__BackingField': [0, 8], '<CardUID>k__BackingField': [1, 12],
      '<UnitIndex>k__BackingField': [2, 16]}},
    tk2dUIToggleButtonGroup: {offset: 944234, count: 6, fields: {
      toggleBtns: [0, 16], selectedIndex: [2, 24]}},
  };
  if (current) {
    golden.ResearchPopup.offset = 779523;
    golden.CardView = {offset: 790050, count: 104, fields: {
      '<CardID>k__BackingField': [53, 248], '<CardData>k__BackingField': [95, 376]}};
    golden.CardScrollerViewNew.offset = 799968; golden.CardScrollerViewNew.count = 61;
    golden.ServiceCardData.offset = 631139;
    golden.tk2dUIToggleButtonGroup.offset = 960044;
  }
  const cardIDOffset = current ? 248 : 260, cardDataOffset = current ? 376 : 388;
  let ordinal = 0, fieldName = 850000;
  for (const [name, definition] of Object.entries(golden)) {
    const klass = 950000 + ordinal * 16000, fields = klass + 1024;
    ordinal++;
    classes[name] = klass;
    text(base + definition.offset, name);
    write(klass + 8, base + definition.offset); write(klass + 64, fields);
    for (const [name, [j, offset]] of Object.entries(definition.fields)) {
      text(fieldName, name); write(fields + j * 20, fieldName);
      write(fields + j * 20 + 12, scenario.field_changed && j === 0 ? offset + 4 : offset);
      fieldName += 128;
    }
  }
  const popup = 1100000, library = 1110000, slots = [1120000, 1121000];
  const viewPointers = [1130000, 1131000, 1132000];
  const viewList = 1150000, viewArray = 1151000, stackList = 1160000, stackArray = 1161000;
  const dataList = 1164000, dataArray = 1165000, tabs = 1173000, tabArray = 1174000;
  const genericClass = 1170000, nativeCached = 1180000, transform = 1190000;
  const ids = scenario.offscreen ? [101, 102, 103, 104, 1400003, 1400021] : [1400003, 1400021, 103];
  const dataPointers = ids.map((_, index) => 1200000 + index * 1024);
  write(popup, classes.ResearchPopup); write(popup + 8, nativeCached);
  write(popup + 124, library); write(popup + 156, slots[0]); write(popup + 184, slots[1]);
  write(popup + 112, tabs); write(tabs, classes.tk2dUIToggleButtonGroup);
  write(tabs + 16, tabArray); write(tabs + 24, scenario.wrong_tab ? 3 : 2);
  write(tabArray, genericClass); write(tabArray + 12, 3);
  write(popup + 252, scenario.chamber || 3); b[popup + 258] = 1;
  write(library, classes.CardScrollerViewNew); write(library + 8, nativeCached); b[library + 42] = 1;
  write(library + 84, viewList); write(library + 88, dataList); write(library + 96, stackList);
  b[library + 185] = scenario.pending_reload ? 1 : 0;
  b[library + 186] = scenario.pending_filter ? 1 : 0;
  f[(library + 108) >>> 2] = 200; f[(library + 120) >>> 2] = 100;
  f[(library + 128) >>> 2] = 0; f[(library + 132) >>> 2] = Math.max(0, (ids.length - 2) * 100);
  if (!scenario.hidden) active.add(popup);
  for (let i = 0; i < slots.length; i++) {
    write(slots[i], classes.CardView); write(slots[i] + 8, nativeCached);
    write(slots[i] + cardIDOffset, (scenario.selected || [0, 0])[i]);
  }
  for (let i = 0; i < ids.length; i++) {
    write(dataPointers[i], classes.ServiceCardData); write(dataPointers[i] + 8, ids[i]);
    write(dataPointers[i] + 12, i + 1); write(dataPointers[i] + 16, i + 100);
    write(dataArray + 16 + i * 4, dataPointers[i]);
    write(stackArray + 16 + i * 8, dataPointers[i]); write(stackArray + 20 + i * 8, 1);
  }
  for (let i = 0; i < viewPointers.length; i++) {
    const p = viewPointers[i]; write(p, classes.CardView); write(p + 8, nativeCached);
    write(viewArray + 16 + i * 4, p); active.add(p);
    allowedChanges.add(p + cardIDOffset); allowedChanges.add(p + cardDataOffset);
  }
  for (const [list, array, count] of [[viewList, viewArray, 3], [stackList, stackArray, scenario.empty_library ? 0 : ids.length], [dataList, dataArray, ids.length]]) {
    write(list, genericClass); write(list + 8, array); write(list + 12, count);
    write(array, genericClass); write(array + 12, count);
  }
  const bind = () => {
    const start = Math.floor(f[(library + 128) >>> 2] / 100);
    const end = Math.min(ids.length, Math.ceil((f[(library + 128) >>> 2] + 200) / 100));
    for (let i = start; i < end; i++) {
      const p = viewPointers[i % viewPointers.length]; write(p + cardIDOffset, ids[i]); write(p + cardDataOffset, dataPointers[i]);
    }
  };
  bind();
  if (scenario.source_mismatch) write(viewPointers[0] + cardDataOffset, dataPointers[1]);
  if (scenario.second_popup) {
    const second = 1300000;
    new Uint8Array(memory, second, 512).set(new Uint8Array(memory, popup, 512)); active.add(second);
  }
  const before = h.slice();
  const scratch = 1400000;
  for (let p = scratch; p < scratch + 48; p += 4) allowedChanges.add(p);
  allowedChanges.add(library + 128);
  for (const slot of slots) { allowedChanges.add(slot + cardIDOffset); allowedChanges.add(slot + cardDataOffset); }
  let iterator = null, startPosition = null, startScroll = 0;
  const vector = p => [f[p >>> 2], f[(p >>> 2) + 1]];
  const handlers = {
    [abi.game_object]: p => { calls.push([abi.game_object]); return p; },
    [abi.active]: p => { calls.push([abi.active]); return active.has(p) ? 1 : 0; },
    [abi.transform]: () => { calls.push([abi.transform]); return transform; },
    [abi.screen_to_local]: (out, _transform, position) => {
      calls.push([abi.screen_to_local]); f[out >>> 2] = f[position >>> 2]; f[(out >>> 2) + 1] = f[(position >>> 2) + 1]; f[(out >>> 2) + 2] = 0;
    },
    [abi.move_card]: (_popup, source, slot, replace) => {
      calls.push([abi.move_card, h[(source + cardIDOffset) >>> 2], slot, replace]); iterator = {source, slot}; return 1410000;
    },
    [abi.start_coroutine]: () => {
      calls.push([abi.start_coroutine]); const destination = slots[iterator.slot - 1];
      write(destination + cardIDOffset, h[(iterator.source + cardIDOffset) >>> 2]); write(destination + cardDataOffset, h[(iterator.source + cardDataOffset) >>> 2]); return 1411000;
    },
    [abi.drag_begin]: (_library, position) => { startPosition = vector(position); startScroll = f[(library + 128) >>> 2]; calls.push([abi.drag_begin, vector(position)]); },
    [abi.drag_move]: (_library, position) => {
      const point = vector(position); calls.push([abi.drag_move, point]);
      f[(library + 128) >>> 2] = Math.max(0, Math.min(f[(library + 132) >>> 2], startScroll - point[0] + startPosition[0]));
    },
    [abi.drag_end]: (_library, position) => { calls.push([abi.drag_end, vector(position)]); },
    [abi.update]: () => { calls.push([abi.update]); },
    [abi.late_update]: () => { calls.push([abi.late_update]); bind(); },
  };
  const module = {
    HEAPU32: h, HEAPU8: b, HEAPF32: f, wasmBinary: new Uint8Array(8), canvas: {width: 600, height: 400},
    asm: {__indirect_function_table: {get: slot => {
      if (!Object.hasOwn(handlers, slot)) throw Error('Forbidden native function requested');
      return handlers[slot];
    }}},
  };
  if (!scenario.no_allocator) {
    const allocate = size => { allocations.push(size); return scratch; };
    const release = pointer => { frees.push(pointer); };
    if (scenario.exported_allocator) { module.asm.malloc = allocate; module.asm.free = release; }
    else { module._malloc = allocate; module._free = release; }
  }
  globalThis.window = {unityInstance: {Module: module}};
  Object.defineProperty(globalThis, 'crypto', {value: {subtle: {digest: async () => Buffer.from(scenario.digest || input.hash, 'hex')}}, configurable: true});
  const result = await eval(input.expression);
  let unchanged = true;
  for (let i = 0; i < h.length; i++) if (before[i] !== h[i] && !allowedChanges.has(i * 4)) unchanged = false;
  process.stdout.write(JSON.stringify({result, calls, selected: slots.map(p => h[(p + cardIDOffset) >>> 2]), allocations, frees,
                                       unchanged_outside_native_handlers: unchanged}));
})().catch(() => { process.stderr.write('Synthetic native UI fixture failed.'); process.exitCode = 1; });
