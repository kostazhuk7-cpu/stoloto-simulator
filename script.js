/**
 * Столото Симулятор v2.0 — Все игры Спортлото + Мультивыбор + Параллельный запуск
 */

// ==================== LOGGER ====================
const Logger = {
    logs: [],
    maxLogs: 10000,
    
    add(type, data) {
        const entry = {
            timestamp: new Date().toISOString(),
            type,
            data: JSON.parse(JSON.stringify(data)) // deep clone
        };
        this.logs.push(entry);
        if (this.logs.length > this.maxLogs) {
            this.logs = this.logs.slice(-this.maxLogs);
        }
        // Также логируем в консоль для отладки
        console.log(`[${type}]`, data);
    },
    
    logDraw(gameKey, drawData) {
        this.add('DRAW', {
            gameKey,
            rng: currentRNGName,
            ...drawData
        });
    },
    
    logValidation(gameKey, validationResult) {
        this.add('VALIDATION', {
            gameKey,
            ...validationResult
        });
    },
    
    logError(gameKey, error) {
        this.add('ERROR', {
            gameKey,
            error: error.message || error,
            stack: error.stack || null
        });
    },
    
    exportJSON() {
        return JSON.stringify({
            exportDate: new Date().toISOString(),
            totalLogs: this.logs.length,
            stats: state.stats,
            logs: this.logs
        }, null, 2);
    },
    
    clear() {
        this.logs = [];
    }
};

// ==================== VALIDATOR ====================
const Validator = {
    validateGame(gameKey, winning, player, result) {
        const game = gameDefinitions[gameKey];
        const errors = [];
        
        // Проверка 1: Правильное количество чисел в выигрышной комбинации
        // Исключение: КЕНО — выпадает 20 чисел, а игрок выбирает 10
        if (game.fields && gameKey !== 'keno') {
            game.fields.forEach((field, i) => {
                const actualCount = winning[i] ? winning[i].length : 0;
                if (actualCount !== field.count) {
                    errors.push(`Неверное количество чисел в поле ${i}: ${actualCount} вместо ${field.count}`);
                }
            });
        } else if (gameKey === 'keno') {
            // Для КЕНО: winning[0] должен содержать 20 чисел
            if (winning[0].length !== 20) {
                errors.push(`Неверное количество чисел в КЕНО: ${winning[0].length} вместо 20`);
            }
        }
        
        // Проверка 2: Диапазон чисел
        if (game.fields) {
            winning.forEach((field, i) => {
                const min = game.fields[i].min;
                const max = game.fields[i].max;
                field.forEach(n => {
                    if (n < min || n > max) {
                        errors.push(`Число ${n} вне диапазона [${min}-${max}]`);
                    }
                });
            });
        }
        
        // Проверка 3: Уникальность чисел
        if (game.fields) {
            winning.forEach((field, i) => {
                const unique = new Set(field);
                if (unique.size !== field.length) {
                    errors.push(`Дублирующиеся числа в поле ${i}`);
                }
            });
        }
        
        // Проверка 4: Корректность совпадений
        if (result.matches && game.fields) {
            if (gameKey === '4x20' || gameKey === 'oxota' || gameKey === 'big' || gameKey === 'rapido') {
                // Для игр с двумя полями: проверяем каждое поле отдельно
                const m1 = winning[0].filter(n => player[0].includes(n)).length;
                const m2 = winning[1].filter(n => player[1].includes(n)).length;
                if (result.matches[0] !== m1 || result.matches[1] !== m2) {
                    errors.push(`Неверное количество совпадений: [${result.matches.join(',')}] вместо [${m1},${m2}]`);
                }
            } else if (gameKey === 'keno') {
                // Для КЕНО: совпадения = сколько чисел игрока есть в 20 выпавших
                const actualMatches = winning[0].filter(n => player[0].includes(n)).length;
                if (result.matches[0] !== actualMatches) {
                    errors.push(`Неверное количество совпадений: ${result.matches[0]} вместо ${actualMatches}`);
                }
            } else {
                // Для остальных игр
                winning.forEach((field, i) => {
                    if (player[i]) {
                        const actualMatches = field.filter(n => player[i].includes(n)).length;
                        const reportedMatches = result.matches[i] !== undefined ? result.matches[i] : result.matches[0];
                        if (actualMatches !== reportedMatches) {
                            errors.push(`Неверное количество совпадений в поле ${i}: ${reportedMatches} вместо ${actualMatches}`);
                        }
                    }
                });
            }
        }
        
        // Проверка 5: Корректность суммы выигрыша
        if (result.isWin && result.prize) {
            const expectedAmount = result.prize.multiplier;
            if (result.winAmount !== expectedAmount) {
                errors.push(`Неверная сумма выигрыша: ${result.winAmount} вместо ${expectedAmount}`);
            }
        }
        
        // Проверка 6: Корректность цены билета
        const expectedPrice = result.comboCount ? game.price * result.comboCount : game.price;
        if (result.ticketPrice !== expectedPrice) {
            errors.push(`Неверная цена билета: ${result.ticketPrice} вместо ${expectedPrice}`);
        }
        
        const isValid = errors.length === 0;
        if (!isValid) {
            Logger.logValidation(gameKey, { isValid, errors });
        }
        
        return { isValid, errors };
    },
    
    validateBingo(gameKey, winning, player, result) {
        const errors = [];
        const moves = winning[0];
        
        // Проверка 1: Правильное количество ходов (90)
        if (moves.length !== 90) {
            errors.push(`Неверное количество ходов: ${moves.length} вместо 90`);
        }
        
        // Проверка 2: Все числа от 1 до 90
        const expectedNumbers = new Set(Array.from({length: 90}, (_, i) => i + 1));
        const actualNumbers = new Set(moves);
        if (expectedNumbers.size !== actualNumbers.size) {
            errors.push(`Неверный набор ходов: ${actualNumbers.size} уникальных вместо 90`);
        }
        
        // Проверка 3: Структура билета игрока (2 поля × 3 строки × 5 чисел)
        if (!Array.isArray(player) || player.length !== 2) {
            errors.push(`Неверная структура билета: ${player.length} полей вместо 2`);
        } else {
            player.forEach((field, fi) => {
                if (!Array.isArray(field) || field.length !== 3) {
                    errors.push(`Неверное количество строк в поле ${fi}: ${field.length} вместо 3`);
                } else {
                    field.forEach((row, ri) => {
                        if (!Array.isArray(row) || row.length !== 5) {
                            errors.push(`Неверное количество чисел в строке ${fi}-${ri}: ${row.length} вместо 5`);
                        }
                    });
                }
            });
        }
        
        // Проверка 4: Конкуренция
        if (result.competition) {
            if (result.competition.totalTickets < 1) {
                errors.push(`Неверное количество билетов: ${result.competition.totalTickets}`);
            }
            if (result.competition.winners < 0) {
                errors.push(`Неверное количество победителей: ${result.competition.winners}`);
            }
        }
        
        const isValid = errors.length === 0;
        if (!isValid) {
            Logger.logValidation(gameKey, { isValid, errors });
        }
        
        return { isValid, errors };
    }
};

// ==================== STATE ====================
const state = {
    isRunning: false,
    simulationTimer: null,
    currentRNG: 'math',
    activeGames: new Set(),
    games: {},
    history: [],
    frequency: {},
    stats: {},
    expandedBet: false,
    expandedSize: {}
};

// ==================== UTILS ====================
function combinations(n, k) {
    if (k > n || k < 0) return 0;
    if (k === 0 || k === n) return 1;
    let res = 1;
    for (let i = 1; i <= k; i++) {
        res = res * (n - i + 1) / i;
    }
    return Math.round(res);
}

function formatOdds(odds) {
    if (odds >= 1000000) return `1:${(odds/1000000).toFixed(1)}M`;
    if (odds >= 1000) return `1:${(odds/1000).toFixed(0)}K`;
    return `1:${odds}`;
}

// ==================== RNG ENGINE ====================
const RNG = {
    math() { return Math.random(); },
    crypto() {
        const arr = new Uint32Array(1);
        crypto.getRandomValues(arr);
        return arr[0] / 4294967296;
    },
    time() {
        let seed = Date.now() + Math.floor(Math.random() * 1000000);
        return () => {
            seed ^= seed << 13; seed ^= seed >> 17; seed ^= seed << 5;
            return (seed >>> 0) / 4294967296;
        };
    },
    hybrid() {
        const arr = new Uint32Array(1); crypto.getRandomValues(arr);
        let seed = arr[0] + Date.now();
        return () => {
            seed ^= seed << 13; seed ^= seed >> 17; seed ^= seed << 5;
            return ((seed >>> 0) + Math.random() * 100000) % 1;
        };
    }
};

const RNG_NAMES = ['math', 'crypto', 'time', 'hybrid'];
const RNG_LABELS = {
    math: 'Math.random',
    crypto: 'Crypto',
    time: 'Time-seed',
    hybrid: 'Hybrid'
};

let currentRNG = RNG.math;
let currentRNGName = 'math';

function pickRandomRNG() {
    const names = RNG_NAMES;
    const idx = Math.floor(Math.random() * names.length);
    const name = names[idx];
    currentRNGName = name;
    if (name === 'time') currentRNG = RNG.time();
    else if (name === 'hybrid') currentRNG = RNG.hybrid();
    else currentRNG = RNG[name];
    return name;
}

function getRandom() { return currentRNG(); }
function getRandomInt(min, max) { return Math.floor(getRandom() * (max - min + 1)) + min; }

function generateUniqueNumbers(count, min, max) {
    const nums = new Set();
    while (nums.size < count) nums.add(getRandomInt(min, max));
    return Array.from(nums).sort((a, b) => a - b);
}

// ==================== GAME DEFINITIONS ====================
const gameDefinitions = {
    '6x45': {
        name: 'Спортлото 6 из 45',
        price: 70,
        fields: [{ count: 6, min: 1, max: 45 }],
        expanded: { min: 6, max: 14 },
        prizes: [
            { match: 6, name: 'Джекпот', multiplier: 1000000 },
            { match: 5, name: '5 из 6', multiplier: 5000 },
            { match: 4, name: '4 из 6', multiplier: 100 },
            { match: 3, name: '3 из 6', multiplier: 10 },
            { match: 2, name: '2 из 6', multiplier: 0 }
        ],
        theory: {
            total: 8145060,
            cats: { 6: { c: 1, o: 8145060 }, 5: { c: 234, o: 34808 }, 4: { c: 11115, o: 733 }, 3: { c: 182780, o: 45 }, 2: { c: 1233765, o: 7 } }
        }
    },
    '5x36': {
        name: 'Спортлото 5 из 36',
        price: 75,
        fields: [{ count: 5, min: 1, max: 36 }, { count: 1, min: 1, max: 4 }],
        expanded: { min: 5, max: 10, perField: true },
        prizes: [
            { match: '5+1', name: 'Суперприз', multiplier: 3000000 },
            { match: '5+0', name: 'Приз', multiplier: 1000000 },
            { match: '4+0', name: '4 из 5', multiplier: 7500 },
            { match: '3+0', name: '3 из 5', multiplier: 2500 },
            { match: '2+0', name: '2 из 5', multiplier: 0 }
        ],
        theory: {
            total: 1507968,
            cats: { '5+1': { c: 1, o: 1507968 }, '5+0': { c: 3, o: 502656 }, '4+1': { c: 75, o: 20106 }, '4+0': { c: 225, o: 6702 }, '3+1': { c: 1800, o: 838 }, '3+0': { c: 5400, o: 279 }, '2+1': { c: 17100, o: 88 }, '2+0': { c: 51300, o: 29 }, '1+1': { c: 61500, o: 25 }, '1+0': { c: 184500, o: 8 }, '0+1': { c: 98280, o: 15 }, '0+0': { c: 294840, o: 5 } }
        }
    },
    '7x49': {
        name: 'Спортлото 7 из 49',
        price: 100,
        fields: [{ count: 7, min: 1, max: 49 }],
        expanded: { min: 7, max: 14 },
        prizes: [
            { match: 7, name: 'Джекпот', multiplier: 1000000 },
            { match: 6, name: '6 из 7', multiplier: 10000 },
            { match: 5, name: '5 из 7', multiplier: 500 },
            { match: 4, name: '4 из 7', multiplier: 20 },
            { match: 3, name: '3 из 7', multiplier: 0 }
        ],
        theory: {
            total: 85900584,
            cats: { 7: { c: 1, o: 85900584 }, 6: { c: 42, o: 2045252 }, 5: { c: 1260, o: 68175 }, 4: { c: 20790, o: 4132 }, 3: { c: 194580, o: 441 } }
        }
    },
    '4x20': {
        name: 'Спортлото 4 из 20',
        price: 300,
        fields: [{ count: 4, min: 1, max: 20 }, { count: 4, min: 1, max: 20 }],
        expanded: { min: 4, max: 10, perField: true },
        prizes: [
            { match: '4+4', name: 'Джекпот', multiplier: 500000 },
            { match: '4+3', name: '4+3 / 3+4', multiplier: 5000 },
            { match: '4+2', name: '4+2 / 2+4 / 3+3', multiplier: 500 },
            { match: '3+2', name: '3+2 / 2+3', multiplier: 50 },
            { match: '2+2', name: '2+2', multiplier: 10 }
        ],
        theory: {
            total: 23474025,
            cats: { '4+4': { c: 1, o: 23474025 }, '4+3': { c: 64, o: 366782 }, '4+2': { c: 1156, o: 20377 }, '3+2': { c: 13824, o: 1698 }, '2+2': { c: 82944, o: 283 } }
        }
    },
    'big': {
        name: 'Большое Спортлото',
        price: 80,
        fields: [{ count: 5, min: 1, max: 50 }, { count: 2, min: 1, max: 10 }],
        expanded: null,
        prizes: [
            { match: '5+2', name: 'Джекпот', multiplier: 1000000 },
            { match: '5+1', name: '5+1', multiplier: 10000 },
            { match: '5+0', name: '5+0', multiplier: 1000 },
            { match: '4+2', name: '4+2', multiplier: 500 },
            { match: '4+1', name: '4+1', multiplier: 100 },
            { match: '3+2', name: '3+2', multiplier: 50 },
            { match: '2+2', name: '2+2', multiplier: 10 }
        ],
        theory: {
            total: 95344200,
            cats: { '5+2': { c: 1, o: 95344200 }, '5+1': { c: 18, o: 5296900 }, '5+0': { c: 36, o: 2648450 }, '4+2': { c: 325, o: 293366 }, '4+1': { c: 5850, o: 16300 }, '3+2': { c: 7800, o: 12224 }, '2+2': { c: 29250, o: 3260 } }
        }
    },
    'allornothing': {
        name: 'Всё или Ничего',
        price: 150,
        fields: [{ count: 12, min: 1, max: 24 }],
        expanded: null,
        prizes: [
            { match: 12, name: 'Суперприз (12)', multiplier: 1000000 },
            { match: 0, name: 'Суперприз (0)', multiplier: 1000000 },
            { match: 11, name: '11 или 1', multiplier: 5000 },
            { match: 1, name: '11 или 1', multiplier: 5000 },
            { match: 10, name: '10 или 2', multiplier: 200 },
            { match: 2, name: '10 или 2', multiplier: 200 },
            { match: 9, name: '9 или 3', multiplier: 20 },
            { match: 3, name: '9 или 3', multiplier: 20 },
            { match: 8, name: '8 или 4', multiplier: 5 },
            { match: 4, name: '8 или 4', multiplier: 5 }
        ],
        theory: {
            total: 2704156,
            cats: { 12: { c: 1, o: 2704156 }, 0: { c: 1, o: 2704156 }, 11: { c: 144, o: 18779 }, 1: { c: 144, o: 18779 }, 10: { c: 4356, o: 621 }, 2: { c: 4356, o: 621 }, 9: { c: 48400, o: 56 }, 3: { c: 48400, o: 56 }, 8: { c: 245025, o: 11 }, 4: { c: 245025, o: 11 } }
        }
    },
    'rapido': {
        name: 'Рапидо',
        price: 30,
        fields: [{ count: 8, min: 1, max: 20 }, { count: 1, min: 1, max: 4 }],
        expanded: { min: 8, max: 12, perField: true },
        prizes: [
            { match: '8+1', name: 'Джекпот', multiplier: 10000 },
            { match: '8+0', name: '8+0', multiplier: 1000 },
            { match: '7+1', name: '7+1', multiplier: 500 },
            { match: '7+0', name: '7+0', multiplier: 50 },
            { match: '6+1', name: '6+1', multiplier: 20 },
            { match: '5+1', name: '5+1', multiplier: 5 },
            { match: '4+1', name: '4+1', multiplier: 2 }
        ],
        theory: {
            total: 503880,
            cats: { '8+1': { c: 1, o: 503880 }, '8+0': { c: 3, o: 167960 }, '7+1': { c: 40, o: 12597 }, '7+0': { c: 120, o: 4199 }, '6+1': { c: 630, o: 800 }, '5+1': { c: 4032, o: 125 }, '4+1': { c: 18480, o: 27 } }
        }
    },
    'oxota': {
        name: 'Охота',
        price: 250,
        fields: [{ count: 4, min: 1, max: 20 }, { count: 4, min: 1, max: 20 }],
        expanded: null,
        prizes: [
            { match: '4+4', name: 'Суперприз', multiplier: 1000000 },
            { match: '4+3', name: '4+3 / 3+4', multiplier: 10000 },
            { match: '4+2', name: '4+2 / 2+4 / 3+3', multiplier: 1000 },
            { match: '3+2', name: '3+2 / 2+3', multiplier: 100 },
            { match: '3+1', name: '3+1 / 1+3', multiplier: 10 },
            { match: '2+2', name: '2+2', multiplier: 5 },
            { match: '2+1', name: '2+1 / 1+2', multiplier: 2 },
            { match: '2+0', name: '2+0 / 0+2', multiplier: 1 },
            { match: '1+1', name: '1+1', multiplier: 0 },
            { match: '1+0', name: '1+0 / 0+1', multiplier: 0 },
            { match: '0+0', name: '0+0', multiplier: 0 }
        ],
        theory: {
            total: 23474025,
            cats: { '4+4': { c: 1, o: 23474025 }, '4+3': { c: 64, o: 366782 }, '4+2': { c: 1156, o: 20377 }, '3+3': { c: 13824, o: 1698 }, '3+2': { c: 82944, o: 283 }, '3+1': { c: 331776, o: 71 }, '2+2': { c: 1244160, o: 19 }, '2+1': { c: 2985984, o: 8 }, '2+0': { c: 2985984, o: 8 }, '1+1': { c: 8957952, o: 3 }, '1+0': { c: 8957952, o: 3 }, '0+0': { c: 8957952, o: 3 } }
        }
    },
    'keno': {
        name: 'КЕНО',
        price: 50,
        fields: [{ count: 10, min: 1, max: 80 }],
        expanded: { min: 1, max: 10 },
        prizes: [
            { match: 10, name: '10 из 10', multiplier: 10000 },
            { match: 9, name: '9 из 10', multiplier: 5000 },
            { match: 8, name: '8 из 10', multiplier: 1000 },
            { match: 7, name: '7 из 10', multiplier: 100 },
            { match: 6, name: '6 из 10', multiplier: 20 },
            { match: 5, name: '5 из 10', multiplier: 5 },
            { match: 4, name: '4 из 10', multiplier: 2 },
            { match: 3, name: '3 из 10', multiplier: 0 },
            { match: 2, name: '2 из 10', multiplier: 0 },
            { match: 1, name: '1 из 10', multiplier: 0 },
            { match: 0, name: '0 из 10', multiplier: 0 }
        ],
        theory: {
            total: 1646492110120,
            cats: { 10: { c: 184756, o: 8911711 }, 9: { c: 10077600, o: 163381 }, 8: { c: 222966900, o: 7384 }, 7: { c: 2863573200, o: 575 }, 6: { c: 24221109900, o: 68 }, 5: { c: 136264914000, o: 12 }, 4: { c: 520676256000, o: 3 }, 3: { c: 1311738120000, o: 1 }, 2: { c: 2076436080000, o: 1 }, 1: { c: 1932530808000, o: 1 }, 0: { c: 753940275000, o: 2 } }
        }
    },
    'ruslotto': {
        name: 'Русское Лото',
        price: 150,
        type: 'bingo',
        moves: 86,
        prizes: [
            { match: 'jackpot', name: 'Джекпот (15 на 15-м)', multiplier: 1000000 },
            { match: 'gold', name: 'Золотой бочонок (≤38)', multiplier: 100000 },
            { match: '30all', name: '30 чисел (бинго)', multiplier: 10000 },
            { match: '15field', name: '15 в поле', multiplier: 1000 },
            { match: '5row', name: '5 в строке', multiplier: 100 }
        ],
        theory: {
            total: 1,
            cats: { 'jackpot': { c: 1, o: 1 }, 'gold': { c: 1, o: 1 }, '30all': { c: 1, o: 1 }, '15field': { c: 1, o: 1 }, '5row': { c: 1, o: 1 } }
        }
    },
    'gzhl': {
        name: 'Жилищная лотерея',
        price: 150,
        type: 'bingo',
        moves: 86,
        prizes: [
            { match: 'superprize', name: 'Суперприз (10 на 10-м)', multiplier: 1000000 },
            { match: '30all', name: '30 чисел (бинго)', multiplier: 10000 },
            { match: '15field', name: '15 в поле', multiplier: 1000 },
            { match: '5row', name: '5 в строке', multiplier: 100 }
        ],
        theory: {
            total: 1,
            cats: { 'superprize': { c: 1, o: 1 }, '30all': { c: 1, o: 1 }, '15field': { c: 1, o: 1 }, '5row': { c: 1, o: 1 } }
        }
    },
    'zodiac': {
        name: 'Зодиак',
        price: 50,
        fields: [{ count: 4, min: 1, max: 99 }],
        expanded: false,
        prizes: [
            { match: 4, name: '4 числа', multiplier: 500000 },
            { match: 3, name: '3 числа', multiplier: 1000 },
            { match: 2, name: '2 числа', multiplier: 300 },
            { match: 1, name: '1 число', multiplier: 50 },
            { match: 0, name: '0 чисел', multiplier: 0 }
        ],
        theory: {
            total: 3764376,
            cats: { 4: { c: 1, o: 3764376 }, 3: { c: 380, o: 9906 }, 2: { c: 26790, o: 140 }, 1: { c: 553660, o: 7 }, 0: { c: 3183545, o: 1 } }
        }
    }
};

// ==================== THEORY VALIDATOR ====================
function validateTheory() {
    const errors = [];
    
    Object.entries(gameDefinitions).forEach(([gameKey, game]) => {
        if (game.type === 'bingo') return; // Бинго проверяется отдельно
        
        let expectedTotal;
        if (gameKey === 'keno') {
            // КЕНО: C(80,10)
            expectedTotal = combinations(80, 10);
        } else if (gameKey === 'allornothing') {
            // Всё или Ничего: C(24,12)
            expectedTotal = combinations(24, 12);
        } else if (gameKey === 'zodiac') {
            // Зодиак: C(99,4)
            expectedTotal = combinations(99, 4);
        } else if (game.fields.length === 1) {
            // Одно поле: C(max, count)
            const f = game.fields[0];
            expectedTotal = combinations(f.max, f.count);
        } else if (game.fields.length === 2) {
            // Два поля: C(max1, count1) * C(max2, count2)
            const f1 = game.fields[0];
            const f2 = game.fields[1];
            expectedTotal = combinations(f1.max, f1.count) * combinations(f2.max, f2.count);
        } else {
            return;
        }
        
        const actualTotal = game.theory.total;
        if (Math.abs(expectedTotal - actualTotal) > 1) {
            errors.push(`${game.name}: theory.total=${actualTotal}, но должно быть ${expectedTotal}`);
        }
        
        // Проверяем сумму категорий
        const sumCats = Object.values(game.theory.cats).reduce((a, c) => a + c.c, 0);
        if (Math.abs(sumCats - actualTotal) > 1) {
            errors.push(`${game.name}: сумма категорий=${sumCats}, но должно быть ${actualTotal}`);
        }
    });
    
    if (errors.length > 0) {
        console.error('[THEORY VALIDATION ERRORS]', errors);
        // Сохраняем ошибки для отображения в UI
        window.theoryValidationErrors = errors;
    } else {
        console.log('[THEORY VALIDATION] Все теоретические данные корректны');
        window.theoryValidationErrors = [];
    }
    
    return errors;
}

// ==================== BINGO COMPETITION ====================
const BINGO_CONFIG = {
    poolSize: 500,         // Количество конкурентных билетов
    prizePoolPercent: 0.50, // 50% от выручки идет в призовой фонд
    minPrize: 50           // Минимальный приз
};

function generateBingoPool(size) {
    const pool = [];
    for (let i = 0; i < size; i++) {
        pool.push(generateBingoTicket());
    }
    return pool;
}

function findBingoWinners(pool, moves, maxMoves, gameKey, playerTicket) {
    const moveIndex = {};
    moves.forEach((num, idx) => moveIndex[num] = idx + 1);

    // Более реалистичные пороги
    const BINGO_LIMITS = {
        ruslotto: { row: 25, field: 50, all: 75, jackpot: 15 },
        gzhl: { row: 25, field: 50, all: 75, superprize: 20 }
    };
    const limits = BINGO_LIMITS[gameKey] || { row: 15, field: 30, all: 60 };

    // Проверяем каждый билет + наш билет
    const allTickets = [...pool, playerTicket];
    const results = allTickets.map((ticket, idx) => {
        const bingo = checkBingoResult(ticket, moves, 90);
        return { idx, bingo, isPlayer: idx === allTickets.length - 1 };
    });

    if (gameKey === 'ruslotto') {
        // Приоритет: полное поле (15) -> 15 в поле (30) -> 30 чисел (60) -> строка (15)
        const jackpot = results.filter(r => r.bingo.fieldMove <= limits.jackpot);
        const all30 = results.filter(r => r.bingo.allMove <= limits.all);
        const field = results.filter(r => r.bingo.fieldMove <= limits.field);
        const row = results.filter(r => r.bingo.rowMove <= limits.row);

        return {
            jackpot: jackpot.length > 0 ? jackpot.sort((a, b) => a.bingo.fieldMove - b.bingo.fieldMove) : [],
            all30: all30.length > 0 ? all30.sort((a, b) => a.bingo.allMove - b.bingo.allMove) : [],
            field: field.length > 0 ? field.sort((a, b) => a.bingo.fieldMove - b.bingo.fieldMove) : [],
            row: row.length > 0 ? row.sort((a, b) => a.bingo.rowMove - b.bingo.rowMove) : [],
            totalTickets: allTickets.length
        };
    } else if (gameKey === 'gzhl') {
        // Жилищная: приоритет: две строки (10) -> 15 в поле (30) -> 30 чисел (60) -> строка (15)
        const superprize = results.filter(r => r.bingo.twoRowsMove <= limits.superprize);
        const all30 = results.filter(r => r.bingo.allMove <= limits.all);
        const field = results.filter(r => r.bingo.fieldMove <= limits.field);
        const row = results.filter(r => r.bingo.rowMove <= limits.row);

        return {
            superprize: superprize.length > 0 ? superprize.sort((a, b) => a.bingo.twoRowsMove - b.bingo.twoRowsMove) : [],
            all30: all30.length > 0 ? all30.sort((a, b) => a.bingo.allMove - b.bingo.allMove) : [],
            field: field.length > 0 ? field.sort((a, b) => a.bingo.fieldMove - b.bingo.fieldMove) : [],
            row: row.length > 0 ? row.sort((a, b) => a.bingo.rowMove - b.bingo.rowMove) : [],
            totalTickets: allTickets.length
        };
    }

    return { totalTickets: allTickets.length };
}

function calculateBingoPrize(winners, gameKey, ticketPrice, matchKey) {
    const totalRevenue = winners.totalTickets * ticketPrice;
    const prizePool = totalRevenue * BINGO_CONFIG.prizePoolPercent;

    // Приз зависит от категории, в которой выиграл наш билет
    const PRIZE_SHARES = {
        ruslotto: { jackpot: 0.5, gold: 0.1, '30all': 0.15, '15field': 0.15, '5row': 0.1 },
        gzhl: { superprize: 0.5, '30all': 0.15, '15field': 0.15, '5row': 0.1, 'none': 0 }
    };
    const shares = PRIZE_SHARES[gameKey] || {};

    // Находим победителей в нужной категории
    let winnerList = [];
    if (matchKey === 'jackpot' || matchKey === 'superprize') {
        winnerList = winners[matchKey] || [];
    } else if (matchKey === '30all') {
        winnerList = winners.all30 || [];
    } else if (matchKey === '15field') {
        winnerList = winners.field || [];
    } else if (matchKey === '5row') {
        winnerList = winners.row || [];
    }

    if (winnerList.length === 0) {
        return { category: matchKey, prize: 0, winners: 0 };
    }

    const share = shares[matchKey] || 0.1;
    const prize = Math.max(Math.floor(prizePool * share / winnerList.length), BINGO_CONFIG.minPrize);

    return { category: matchKey, prize, winners: winnerList.length };
}

// ==================== BINGO UTILS ====================
function generateBingoTicket() {
    const allNumbers = Array.from({length: 90}, (_, i) => i + 1);
    for (let i = allNumbers.length - 1; i > 0; i--) {
        const j = Math.floor(getRandom() * (i + 1));
        [allNumbers[i], allNumbers[j]] = [allNumbers[j], allNumbers[i]];
    }
    const ticket = [];
    for (let field = 0; field < 2; field++) {
        const fieldNumbers = allNumbers.slice(field * 15, (field + 1) * 15);
        const rows = [];
        for (let row = 0; row < 3; row++) {
            rows.push(fieldNumbers.slice(row * 5, (row + 1) * 5).sort((a, b) => a - b));
        }
        ticket.push(rows);
    }
    return ticket;
}

function checkBingoResult(ticket, moves, maxMoves) {
    const moveIndex = {};
    moves.forEach((num, idx) => moveIndex[num] = idx + 1);

    let bestRowMove = Infinity;
    for (const field of ticket) {
        for (const row of field) {
            const maxMove = Math.max(...row.map(n => moveIndex[n]));
            if (maxMove < bestRowMove) bestRowMove = maxMove;
        }
    }

    let bestFieldMove = Infinity;
    for (const field of ticket) {
        const allNumbers = field.flat();
        const maxMove = Math.max(...allNumbers.map(n => moveIndex[n]));
        if (maxMove < bestFieldMove) bestFieldMove = maxMove;
    }

    const allTicketNumbers = ticket.flat().flat();
    const allMove = Math.max(...allTicketNumbers.map(n => moveIndex[n]));

    let bestTwoRowsMove = Infinity;
    for (const field of ticket) {
        for (let r1 = 0; r1 < 3; r1++) {
            for (let r2 = 0; r2 < 3; r2++) {
                if (r1 === r2) continue;
                const nums = [...field[r1], ...field[r2]];
                const maxMove = Math.max(...nums.map(n => moveIndex[n]));
                if (maxMove < bestTwoRowsMove) bestTwoRowsMove = maxMove;
            }
        }
    }

    return { rowMove: bestRowMove, fieldMove: bestFieldMove, allMove, twoRowsMove: bestTwoRowsMove };
}

// ==================== GAME ENGINE ====================
const GameEngine = {
    runDraw(gameKey, expandedCount) {
        const game = gameDefinitions[gameKey];
        let counts = game.fields ? game.fields.map(f => f.count) : [];
        
        if (expandedCount && state.expandedBet && game.fields) {
            counts = game.fields.map((f, i) => {
                if (game.expanded && game.expanded.perField) {
                    return expandedCount[i] || f.count;
                }
                return expandedCount[0] || f.count;
            });
        }
        
        let winning;
        if (gameKey === 'keno') {
            winning = [generateUniqueNumbers(20, 1, 80)];
        } else if (game.type === 'bingo') {
            const allNumbers = Array.from({length: 90}, (_, i) => i + 1);
            for (let i = allNumbers.length - 1; i > 0; i--) {
                const j = Math.floor(getRandom() * (i + 1));
                [allNumbers[i], allNumbers[j]] = [allNumbers[j], allNumbers[i]];
            }
            // Генерируем пул конкурентов (наш билет будет добавлен при проверке)
            const pool = generateBingoPool(BINGO_CONFIG.poolSize);
            winning = [allNumbers, pool];
        } else {
            winning = game.fields.map((f, i) => generateUniqueNumbers(counts[i] || f.count, f.min, f.max));
        }
        return { game, winning, counts };
    },

    checkResult(gameKey, winning, player, counts) {
        const game = gameDefinitions[gameKey];
        let matches = [];
        let matchKey = '';

        if (gameKey === 'keno') {
            const m = winning[0].filter(n => player[0].includes(n)).length;
            matches = [m];
            matchKey = m;
        } else if (gameKey === '5x36') {
            const m1 = winning[0].filter(n => player[0].includes(n)).length;
            const m2 = winning[1].filter(n => player[1].includes(n)).length;
            matches = [m1, m2];
            matchKey = `${m1}+${m2}`;
        } else if (gameKey === '4x20' || gameKey === 'oxota') {
            const m1 = winning[0].filter(n => player[0].includes(n)).length;
            const m2 = winning[1].filter(n => player[1].includes(n)).length;
            matches = [m1, m2];
            if (m1 === 4 && m2 === 4) matchKey = '4+4';
            else if ((m1 === 4 && m2 === 3) || (m1 === 3 && m2 === 4)) matchKey = '4+3';
            else if ((m1 === 4 && m2 === 2) || (m1 === 2 && m2 === 4) || (m1 === 3 && m2 === 3)) matchKey = '4+2';
            else if ((m1 === 3 && m2 === 2) || (m1 === 2 && m2 === 3)) matchKey = '3+2';
            else if ((m1 === 3 && m2 === 1) || (m1 === 1 && m2 === 3)) matchKey = '3+1';
            else if (m1 === 2 && m2 === 2) matchKey = '2+2';
            else if ((m1 === 2 && m2 === 1) || (m1 === 1 && m2 === 2)) matchKey = '2+1';
            else if ((m1 === 2 && m2 === 0) || (m1 === 0 && m2 === 2)) matchKey = '2+0';
            else if (m1 === 1 && m2 === 1) matchKey = '1+1';
            else if ((m1 === 1 && m2 === 0) || (m1 === 0 && m2 === 1)) matchKey = '1+0';
            else matchKey = '0+0';
        } else if (gameKey === 'rapido') {
            const m1 = winning[0].filter(n => player[0].includes(n)).length;
            const m2 = winning[1].filter(n => player[1].includes(n)).length;
            matches = [m1, m2];
            matchKey = `${m1}+${m2}`;
        } else if (gameKey === 'big') {
            const m1 = winning[0].filter(n => player[0].includes(n)).length;
            const m2 = winning[1].filter(n => player[1].includes(n)).length;
            matches = [m1, m2];
            matchKey = `${m1}+${m2}`;
        } else if (game.type === 'bingo') {
            const moves = winning[0];
            const pool = winning[1];
            // Более реалистичные пороги для бинго
            const BINGO_LIMITS = {
                ruslotto: { row: 25, field: 50, all: 75, jackpot: 15 },
                gzhl: { row: 25, field: 50, all: 75, superprize: 20 }
            };
            const limits = BINGO_LIMITS[gameKey] || { row: 15, field: 30, all: 60 };
            const bingo = checkBingoResult(player, moves, 90);
            matches = [bingo.rowMove, bingo.fieldMove, bingo.allMove];

            // Находим победителей среди всех билетов (включая наш)
            const winners = findBingoWinners(pool, moves, 90, gameKey, player);

            // Проверяем, есть ли у нас выигрышная комбинация по реалистичным порогам
            let hasWin = false;
            if (gameKey === 'ruslotto') {
                if (bingo.fieldMove <= limits.jackpot) { matchKey = 'jackpot'; hasWin = true; }
                else if (bingo.fieldMove <= limits.field) { matchKey = '15field'; hasWin = true; }
                else if (bingo.allMove <= limits.all) { matchKey = '30all'; hasWin = true; }
                else if (bingo.rowMove <= limits.row) { matchKey = '5row'; hasWin = true; }
                else matchKey = 'none';
            } else if (gameKey === 'gzhl') {
                if (bingo.twoRowsMove <= limits.superprize) { matchKey = 'superprize'; hasWin = true; }
                else if (bingo.fieldMove <= limits.field) { matchKey = '15field'; hasWin = true; }
                else if (bingo.allMove <= limits.all) { matchKey = '30all'; hasWin = true; }
                else if (bingo.rowMove <= limits.row) { matchKey = '5row'; hasWin = true; }
                else matchKey = 'none';
            } else {
                matchKey = 'none';
            }

            // Рассчитываем приз для нашей категории
            const prize = calculateBingoPrize(winners, gameKey, game.price, matchKey);

            // Возвращаем результат с конкуренцией
            return {
                matches,
                matchKey,
                prize: hasWin ? { name: prize.category, multiplier: prize.prize } : null,
                winAmount: hasWin ? prize.prize : 0,
                isWin: hasWin,
                comboCount: 1,
                ticketPrice: game.price,
                competition: {
                    totalTickets: winners.totalTickets,
                    winners: prize.winners,
                    category: prize.category
                }
            };
        } else if (gameKey === 'zodiac') {
            const m = winning[0].filter(n => player[0].includes(n)).length;
            matches = [m];
            matchKey = m;
        } else if (gameKey === 'allornothing') {
            const m = winning[0].filter(n => player[0].includes(n)).length;
            matches = [m];
            matchKey = m;
        } else {
            const m = winning[0].filter(n => player[0].includes(n)).length;
            matches = [m];
            matchKey = m;
        }

        const prize = game.prizes.find(p => p.match === matchKey);
        const comboCount = counts ? this.calcComboCount(gameKey, counts) : 1;
        const ticketPrice = game.price * comboCount;
        
        return {
            matches,
            matchKey,
            prize: prize || null,
            winAmount: prize ? prize.multiplier : 0,
            isWin: !!(prize && prize.multiplier > 0),
            comboCount,
            ticketPrice
        };
    },

    calcComboCount(gameKey, counts) {
        const game = gameDefinitions[gameKey];
        if (!game.fields) return 1;
        let combos = 1;
        game.fields.forEach((f, i) => {
            const c = counts[i] || f.count;
            if (gameKey === 'keno') {
                combos *= c >= f.count ? combinations(c, f.count) : 1;
            } else {
                combos *= combinations(c, f.count);
            }
        });
        return combos;
    }
};

// ==================== STRATEGIES ====================
const Strategies = {
    random(gameKey, counts) {
        const game = gameDefinitions[gameKey];
        if (game.type === 'bingo') return generateBingoTicket();
        return game.fields.map((f, i) => generateUniqueNumbers(counts[i] || f.count, f.min, f.max));
    },

    frequency(gameKey, counts) {
        const game = gameDefinitions[gameKey];
        if (game.type === 'bingo') return generateBingoTicket();
        const allNumbers = [];
        state.history.forEach(draw => {
            if (draw.gameKey === gameKey) {
                draw.winning.forEach(field => allNumbers.push(...field));
            }
        });
        if (allNumbers.length === 0) return this.random(gameKey, counts);

        const freq = {};
        for (let i = game.fields[0].min; i <= game.fields[0].max; i++) freq[i] = 0;
        allNumbers.forEach(n => { if (freq[n] !== undefined) freq[n]++; });

        const sorted = Object.entries(freq).sort((a, b) => b[1] - a[1]).map(([n]) => parseInt(n));
        return game.fields.map((f, i) => sorted.slice(0, counts[i] || f.count));
    },

    cold(gameKey, counts) {
        const game = gameDefinitions[gameKey];
        if (game.type === 'bingo') return generateBingoTicket();
        const allNumbers = [];
        state.history.forEach(draw => {
            if (draw.gameKey === gameKey) {
                draw.winning.forEach(field => allNumbers.push(...field));
            }
        });
        if (allNumbers.length === 0) return this.random(gameKey, counts);

        const freq = {};
        for (let i = game.fields[0].min; i <= game.fields[0].max; i++) freq[i] = 0;
        allNumbers.forEach(n => { if (freq[n] !== undefined) freq[n]++; });

        const sorted = Object.entries(freq).sort((a, b) => a[1] - b[1]).map(([n]) => parseInt(n));
        return game.fields.map((f, i) => sorted.slice(0, counts[i] || f.count));
    },

    mixed(gameKey, counts) {
        const game = gameDefinitions[gameKey];
        if (game.type === 'bingo') return generateBingoTicket();
        const hot = this.frequency(gameKey, counts);
        const rand = this.random(gameKey, counts);
        return game.fields.map((f, i) => {
            const combined = [...hot[i], ...rand[i]];
            const unique = [...new Set(combined)];
            return unique.slice(0, counts[i] || f.count).sort((a, b) => a - b);
        });
    }
};

function getPlayerNumbers(gameKey, strategy, counts) {
    return Strategies[strategy](gameKey, counts);
}

// ==================== PARALLEL SIMULATION ====================
let simulationTimer = null;
let isRunningInfinite = false;

function toggleSimulation() {
    if (isRunningInfinite) stopSimulation();
    else startSimulation();
}

function startSimulation() {
    if (state.isRunning) return;
    isRunningInfinite = true;
    
    const btn = document.getElementById('toggleBtn');
    btn.textContent = '⏹ Остановить';
    btn.classList.add('btn-active');
    
    const interval = parseInt(document.getElementById('intervalMs').value) || 50;
    const gameKeys = getActiveGameKeys();
    
    document.getElementById('gameContainer').querySelectorAll('input, select').forEach(el => {
        if (!el.id.includes('toggle') && !el.id.includes('interval')) el.disabled = true;
    });

    runParallelDraw(gameKeys);
    simulationTimer = setInterval(() => {
        if (!state.isRunning) runParallelDraw(gameKeys);
    }, interval);
}

function stopSimulation() {
    isRunningInfinite = false;
    clearInterval(simulationTimer);
    simulationTimer = null;
    
    const btn = document.getElementById('toggleBtn');
    btn.textContent = '▶ Запустить';
    btn.classList.remove('btn-active');
    
    document.getElementById('gameContainer').querySelectorAll('input, select').forEach(el => el.disabled = false);
}

function getActiveGameKeys() {
    return Array.from(document.querySelectorAll('.game-toggle:checked')).map(cb => cb.dataset.game);
}

function getExpandedCounts(gameKey) {
    const game = gameDefinitions[gameKey];
    if (!game.fields || !game.expanded || !state.expandedBet) return game.fields ? game.fields.map(f => f.count) : [1];
    
    const container = document.getElementById(`game-${gameKey}`);
    if (!container) return game.fields.map(f => f.count);
    
    if (game.expanded.perField) {
        return game.fields.map((f, i) => {
            const input = container.querySelector(`.expanded-count[data-field="${i}"]`);
            return parseInt(input?.value) || f.count;
        });
    } else {
        const input = container.querySelector('.expanded-count');
        const count = parseInt(input?.value) || game.expanded.min;
        return game.fields.map(() => count);
    }
}

async function runParallelDraw(gameKeys) {
    if (state.isRunning) return;
    state.isRunning = true;
    
    const rngSource = document.getElementById('rngSource').value;
    const strategy = document.getElementById('strategy').value;
    
    // Random RNG selection for each draw
    let usedRNG;
    if (rngSource === 'random') {
        usedRNG = pickRandomRNG();
    } else {
        usedRNG = rngSource;
        currentRNGName = rngSource;
        if (rngSource === 'time') currentRNG = RNG.time();
        else if (rngSource === 'hybrid') currentRNG = RNG.hybrid();
        else currentRNG = RNG[rngSource];
    }

    const results = [];
    
    for (const gameKey of gameKeys) {
        const counts = getExpandedCounts(gameKey);
        const { winning } = GameEngine.runDraw(gameKey, counts);
        const player = getPlayerNumbers(gameKey, strategy, counts);
        const result = GameEngine.checkResult(gameKey, winning, player, counts);
        
        // Валидация и логирование
        try {
            if (gameDefinitions[gameKey].type === 'bingo') {
                const validation = Validator.validateBingo(gameKey, winning, player, result);
                if (!validation.isValid) {
                    console.error(`[VALIDATION ERROR] ${gameKey}:`, validation.errors);
                }
            } else {
                const validation = Validator.validateGame(gameKey, winning, player, result);
                if (!validation.isValid) {
                    console.error(`[VALIDATION ERROR] ${gameKey}:`, validation.errors);
                }
            }
            
            // Логирование тиража
            Logger.logDraw(gameKey, {
                rng: currentRNGName,
                winning: gameDefinitions[gameKey].type === 'bingo' ? winning[0].slice(0, 20) : winning,
                player: gameDefinitions[gameKey].type === 'bingo' ? player : player,
                matches: result.matches,
                matchKey: result.matchKey,
                isWin: result.isWin,
                winAmount: result.winAmount,
                ticketPrice: result.ticketPrice,
                competition: result.competition || null
            });
        } catch (e) {
            Logger.logError(gameKey, e);
        }
        
        // Update stats
        if (!state.stats[gameKey]) {
            state.stats[gameKey] = { total: 0, wins: 0, losses: 0, categories: {}, spent: 0, won: 0 };
        }
        const s = state.stats[gameKey];
        s.total++;
        s.spent += result.ticketPrice;
        
        if (result.isWin) {
            s.wins++;
            s.won += result.winAmount;
            const catKey = result.matchKey.toString();
            s.categories[catKey] = (s.categories[catKey] || 0) + 1;
        } else {
            s.losses++;
        }

        // Update frequency
        if (gameDefinitions[gameKey].type === 'bingo') {
            // For bingo, winning = [moves, pool]
            const moves = winning[0];
            moves.forEach(n => {
                if (!state.frequency[gameKey]) state.frequency[gameKey] = {};
                state.frequency[gameKey][n] = (state.frequency[gameKey][n] || 0) + 1;
            });
        } else {
            winning.forEach(field => {
                field.forEach(n => {
                    if (!state.frequency[gameKey]) state.frequency[gameKey] = {};
                    state.frequency[gameKey][n] = (state.frequency[gameKey][n] || 0) + 1;
                });
            });
        }

        state.history.push({ gameKey, winning, player, result, counts, timestamp: Date.now() });
        results.push({ gameKey, winning, player, result, counts });
    }

    // Update UI
    results.forEach(r => updateGameUI(r.gameKey, r.winning, r.player, r.result));
    updateSummaryTable();
    updateLog(results);
    
    if (isRunningInfinite) {
        document.getElementById('totalDraws').textContent = Object.values(state.stats).reduce((a, s) => a + s.total, 0);
    }
    
    state.isRunning = false;
}

// ==================== UI UPDATES ====================
function updateGameUI(gameKey, winning, player, result) {
    const container = document.getElementById(`game-${gameKey}`);
    if (!container) return;

    const winningDiv = container.querySelector('.winning-numbers');
    const playerDiv = container.querySelector('.player-numbers');
    const resultDiv = container.querySelector('.game-result');
    const statsDiv = container.querySelector('.game-mini-stats');
    const game = gameDefinitions[gameKey];

    winningDiv.innerHTML = '';
    if (game.type === 'bingo') {
        // Show first 15 moves as "winning numbers"
        const moves = winning[0];
        moves.slice(0, 15).forEach(n => {
            const ball = document.createElement('span');
            ball.className = 'mini-ball win';
            ball.textContent = n;
            winningDiv.appendChild(ball);
        });
    } else {
        winning.forEach((field, fi) => {
            field.forEach(n => {
                const ball = document.createElement('span');
                ball.className = 'mini-ball win';
                ball.textContent = n;
                winningDiv.appendChild(ball);
            });
        });
    }

    playerDiv.innerHTML = '';
    if (game.type === 'bingo') {
        const ticket = player;
        const moves = winning[0];
        const moveSet = new Set(moves.slice(0, 15));
        ticket.forEach((field, fi) => {
            const fieldDiv = document.createElement('div');
            fieldDiv.style.marginBottom = '3px';
            field.forEach((row, ri) => {
                row.forEach(n => {
                    const ball = document.createElement('span');
                    const isMatch = moveSet.has(n);
                    ball.className = `mini-ball ${isMatch ? 'match' : 'player'}`;
                    ball.textContent = n;
                    ball.style.width = '18px';
                    ball.style.height = '18px';
                    ball.style.fontSize = '0.65em';
                    fieldDiv.appendChild(ball);
                });
                if (ri < 2) {
                    const br = document.createElement('br');
                    fieldDiv.appendChild(br);
                }
            });
            playerDiv.appendChild(fieldDiv);
        });
    } else {
        player.forEach((field, fi) => {
            field.forEach(n => {
                const ball = document.createElement('span');
                const isMatch = winning[fi] && winning[fi].includes(n);
                ball.className = `mini-ball ${isMatch ? 'match' : 'player'}`;
                ball.textContent = n;
                playerDiv.appendChild(ball);
            });
        });
    }

    const s = state.stats[gameKey];
    const roi = s.spent > 0 ? ((s.won - s.spent) / s.spent * 100).toFixed(1) : 0;
    
    if (game.type === 'bingo') {
        const [rowMove, fieldMove, allMove] = result.matches;
        const competition = result.competition || {};
        if (result.isWin) {
            resultDiv.innerHTML = `<span class="win-text">${result.prize.name}: +${result.winAmount}₽ (конкурентов: ${competition.totalTickets || 0}, победителей: ${competition.winners || 0})</span>`;
        } else {
            resultDiv.innerHTML = `<span class="loss-text">строка ${rowMove}, поле ${fieldMove} (конкурентов: ${competition.totalTickets || 0})</span>`;
        }
    } else {
        resultDiv.innerHTML = result.isWin 
            ? `<span class="win-text">${result.prize.name}: +${result.winAmount}₽</span>`
            : `<span class="loss-text">${result.matches.join('+')} совп.</span>`;
    }

    statsDiv.innerHTML = `
        <div>Тиражей: ${s.total}</div>
        <div>Выигрышей: ${s.wins}</div>
        <div>ROI: <span class="${roi >= 0 ? 'pos' : 'neg'}">${roi}%</span></div>
        <div>Баланс: ${s.won - s.spent}₽</div>
    `;
}

function checkEmpiricalVsTheory() {
    const warnings = [];
    const allKeys = Object.keys(gameDefinitions);
    
    allKeys.forEach(key => {
        const game = gameDefinitions[key];
        const s = state.stats[key] || { total: 0, wins: 0, categories: {} };
        
        // Проверяем только если было достаточно тиражей (минимум 100)
        if (s.total < 100) return;
        
        const theory = game.theory;
        const totalCats = Object.values(theory.cats).reduce((a, c) => a + c.c, 0);
        
        // Проверяем каждую категорию
        Object.entries(theory.cats).forEach(([catKey, catData]) => {
            const expectedFreq = catData.c / totalCats; // теоретическая частота
            const empiricalCount = s.categories[catKey] || 0;
            const empiricalFreq = empiricalCount / s.total;
            
            // Если расхождение > 50% и категория достаточно частая (>1%)
            if (expectedFreq > 0.01 && empiricalCount > 0) {
                const diff = Math.abs(empiricalFreq - expectedFreq) / expectedFreq;
                if (diff > 0.5) {
                    warnings.push({
                        game: game.name,
                        category: catKey,
                        expected: (expectedFreq * 100).toFixed(2) + '%',
                        actual: (empiricalFreq * 100).toFixed(2) + '%',
                        diff: (diff * 100).toFixed(0) + '%',
                        draws: s.total
                    });
                }
            }
        });
    });
    
    return warnings;
}

function updateSummaryTable() {
    const tbody = document.getElementById('summaryTable');
    if (!tbody) return;
    
    let html = '';
    const allKeys = Object.keys(gameDefinitions);
    
    allKeys.forEach(key => {
        const game = gameDefinitions[key];
        const s = state.stats[key] || { total: 0, wins: 0, spent: 0, won: 0 };
        const winRate = s.total > 0 ? (s.wins / s.total * 100).toFixed(1) : 0;
        const roi = s.spent > 0 ? ((s.won - s.spent) / s.spent * 100).toFixed(1) : 0;
        const avgWin = s.wins > 0 ? (s.won / s.wins).toFixed(0) : 0;
        const theory = game.theory;
        const jackpotKey = game.prizes[0].match;
        const jackpotOdds = theory.cats[jackpotKey] ? theory.cats[jackpotKey].o : theory.cats[Object.keys(theory.cats).pop()].o;
        const anyWinOdds = Math.round(theory.total / Object.values(theory.cats).reduce((a, c) => a + c.c, 0));
        
        html += `<tr>
            <td>${game.name}</td>
            <td>${game.price}₽</td>
            <td>${s.total}</td>
            <td>${s.wins}</td>
            <td>${winRate}%</td>
            <td>${formatOdds(jackpotOdds)}</td>
            <td>${formatOdds(anyWinOdds)}</td>
            <td>${avgWin}₽</td>
            <td>${s.won}₽</td>
            <td class="${roi >= 0 ? 'pos' : 'neg'}">${roi}%</td>
            <td>${s.won - s.spent}₽</td>
        </tr>`;
    });
    
    tbody.innerHTML = html;
    updateAnalysis();
    renderWarnings();
}

function renderWarnings() {
    const section = document.getElementById('warningsSection');
    const box = document.getElementById('warningsBox');
    if (!section || !box) return;

    const warnings = checkEmpiricalVsTheory();
    if (warnings.length === 0) {
        section.style.display = 'none';
        return;
    }

    section.style.display = '';
    let html = '<div class="analysis-item" style="margin-bottom:8px;font-weight:600;color:#ffcc00;">⚠ Расхождение эмпирических и теоретических данных обнаружено (>{50}%):</div>';
    warnings.forEach(w => {
        html += `<div class="analysis-item" style="margin-bottom:4px;">
            <span class="label"><b>${w.game}</b>, категория «${w.category}»:</span>
            <span class="value">ожидалось ${w.expected}, получено ${w.actual} (расхождение ${w.diff}) за ${w.draws} тиражей</span>
        </div>`;
    });
    box.innerHTML = html;
}

function updateAnalysis() {
    const allKeys = Object.keys(gameDefinitions);
    let bestROI = null;
    let bestROIVal = -Infinity;
    let bestWinRate = null;
    let bestWinRateVal = -Infinity;
    let bestJackpot = null;
    let bestJackpotVal = Infinity;
    let cheapest = null;
    let cheapestVal = Infinity;

    allKeys.forEach(key => {
        const game = gameDefinitions[key];
        const s = state.stats[key] || { total: 0, wins: 0, spent: 0, won: 0 };
        
        const roi = s.spent > 0 ? ((s.won - s.spent) / s.spent * 100) : -Infinity;
        const winRate = s.total > 0 ? (s.wins / s.total * 100) : 0;
        const jackpotKey = game.prizes[0].match;
        const jackpotOdds = game.theory.cats[jackpotKey] ? game.theory.cats[jackpotKey].o : game.theory.cats[Object.keys(game.theory.cats).pop()].o;
        
        if (roi > bestROIVal) {
            bestROIVal = roi;
            bestROI = game.name;
        }
        if (winRate > bestWinRateVal) {
            bestWinRateVal = winRate;
            bestWinRate = game.name;
        }
        if (jackpotOdds < bestJackpotVal) {
            bestJackpotVal = jackpotOdds;
            bestJackpot = game.name;
        }
        if (game.price < cheapestVal) {
            cheapestVal = game.price;
            cheapest = game.name;
        }
    });

    const bestROIElem = document.getElementById('bestROI');
    if (bestROIElem) bestROIElem.textContent = bestROI ? `${bestROI} (${bestROIVal.toFixed(1)}%)` : '—';
    
    const bestWinRateElem = document.getElementById('bestWinRate');
    if (bestWinRateElem) bestWinRateElem.textContent = bestWinRate ? `${bestWinRate} (${bestWinRateVal.toFixed(1)}%)` : '—';
    
    const cheapestElem = document.getElementById('cheapest');
    if (cheapestElem) cheapestElem.textContent = cheapest ? `${cheapest} (${cheapestVal}₽)` : '—';
    
    const bestJackpotElem = document.getElementById('bestJackpot');
    if (bestJackpotElem) bestJackpotElem.textContent = bestJackpot ? `${bestJackpot} (1:${formatOdds(bestJackpotVal).replace('1:', '')})` : '—';
}

function updateLog(results) {
    const log = document.getElementById('log');
    if (!log) return;
    
    results.forEach(r => {
        const entry = document.createElement('div');
        entry.className = `log-entry ${r.result.isWin ? 'win' : ''}`;
        const game = gameDefinitions[r.gameKey];
        entry.textContent = `[${game.name}] ${r.result.matches.join('+')} — ${r.result.isWin ? '+' + r.result.winAmount + '₽' : 'проигрыш'}`;
        log.insertBefore(entry, log.firstChild);
        while (log.children.length > 50) log.removeChild(log.lastChild);
    });
}

// ==================== EXPORT ====================
function exportCSV() {
    let csv = 'Игра,Тираж,Выигрышная,Игрока,Совпадения,Результат,Сумма,Цена билета\n';
    state.history.forEach((draw, i) => {
        const game = gameDefinitions[draw.gameKey];
        const winning = draw.winning.map(f => f.join(' ')).join(' | ');
        const player = draw.player.map(f => f.join(' ')).join(' | ');
        const result = draw.result.isWin ? 'WIN' : 'LOSS';
        csv += `${game.name},${i+1},${winning},${player},${draw.result.matches.join('+')},${result},${draw.result.winAmount},${draw.result.ticketPrice}\n`;
    });
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = `stoloto_all_games_${Date.now()}.csv`;
    link.click();
}

function exportJSON() {
    const json = Logger.exportJSON();
    const blob = new Blob([json], { type: 'application/json' });
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = `stoloto_logs_${Date.now()}.json`;
    link.click();
}

function runValidation() {
    const results = [];
    Object.keys(gameDefinitions).forEach(gameKey => {
        const game = gameDefinitions[gameKey];
        console.log(`[Валидация] Проверка ${game.name}...`);
        
        // Генерируем тестовый тираж
        const { winning } = GameEngine.runDraw(gameKey, game.fields ? game.fields.map(f => f.count) : []);
        const player = getPlayerNumbers(gameKey, 'random', game.fields ? game.fields.map(f => f.count) : []);
        const result = GameEngine.checkResult(gameKey, winning, player, game.fields ? game.fields.map(f => f.count) : []);
        
        let validation;
        if (game.type === 'bingo') {
            validation = Validator.validateBingo(gameKey, winning, player, result);
        } else {
            validation = Validator.validateGame(gameKey, winning, player, result);
        }
        
        results.push({
            game: game.name,
            gameKey,
            isValid: validation.isValid,
            errors: validation.errors
        });
        
        if (validation.isValid) {
            console.log(`[Валидация] ${game.name}: OK`);
        } else {
            console.error(`[Валидация] ${game.name}: ОШИБКИ`, validation.errors);
        }
    });
    
    // Выводим сводку
    const valid = results.filter(r => r.isValid);
    const invalid = results.filter(r => !r.isValid);
    
    alert(`Валидация завершена:
✅ Пройдено: ${valid.length}/${results.length}
❌ Ошибок: ${invalid.length}/${results.length}
${invalid.map(r => `- ${r.game}: ${r.errors.join(', ')}`).join('\n')}`);
    
    return results;
}

// ==================== BUILD UI ====================
function buildGameCards() {
    const container = document.getElementById('gameContainer');
    if (!container) return;
    
    let html = '';
    Object.entries(gameDefinitions).forEach(([key, game]) => {
        const hasExpanded = game.expanded && typeof game.expanded === 'object';
        html += `
            <div class="game-card" id="game-${key}">
                <div class="game-header">
                    <label class="game-toggle-label">
                        <input type="checkbox" class="game-toggle" data-game="${key}" checked>
                        <span class="game-name">${game.name}</span>
                    </label>
                    <span class="game-price">${game.price}₽</span>
                </div>
                <div class="game-body">
                    <div class="numbers-row">
                        <div class="numbers-group">
                            <label>ГСЧ:</label>
                            <div class="winning-numbers"></div>
                        </div>
                        <div class="numbers-group">
                            <label>Игрок:</label>
                            <div class="player-numbers"></div>
                        </div>
                    </div>
                    <div class="game-result"></div>
                    <div class="game-mini-stats"></div>
                    ${hasExpanded ? `
                        <div class="expanded-controls" style="display: ${state.expandedBet ? 'block' : 'none'}">
                            <label>Развёрнутая ставка:</label>
                            ${game.expanded.perField ? 
                                game.fields.map((f, i) => `
                                    <input type="number" class="expanded-count" data-field="${i}" 
                                        value="${f.count}" min="${f.count}" max="${game.expanded.max}" 
                                        title="Поле ${i+1}: ${f.count}-${game.expanded.max} чисел">
                                `).join('')
                                : `<input type="number" class="expanded-count" value="${game.fields[0].count}" min="${game.expanded.min}" max="${game.expanded.max}">`
                            }
                            <span class="combo-info"></span>
                        </div>
                    ` : ''}
                </div>
            </div>
        `;
    });
    container.innerHTML = html;
}

// ==================== EVENT LISTENERS ====================
if (typeof document !== 'undefined') {
    document.addEventListener('DOMContentLoaded', () => {
        // Проверяем теоретические данные при старте
        validateTheory();
        
        buildGameCards();
        updateSummaryTable();
        
        document.getElementById('toggleBtn').addEventListener('click', toggleSimulation);
        
        document.getElementById('clearBtn').addEventListener('click', () => {
            state.history = [];
            state.frequency = {};
            state.stats = {};
            Logger.clear();
            Object.keys(gameDefinitions).forEach(key => {
                state.stats[key] = { total: 0, wins: 0, losses: 0, categories: {}, spent: 0, won: 0 };
            });
            document.getElementById('log').innerHTML = '';
            document.querySelectorAll('.winning-numbers, .player-numbers, .game-result, .game-mini-stats').forEach(el => el.innerHTML = '');
            updateSummaryTable();
            document.getElementById('totalDraws').textContent = '0';
        });

        document.getElementById('exportBtn').addEventListener('click', exportCSV);
        document.getElementById('exportJsonBtn').addEventListener('click', exportJSON);
        document.getElementById('validateBtn').addEventListener('click', runValidation);
        
        document.getElementById('expandedToggle').addEventListener('change', (e) => {
            state.expandedBet = e.target.checked;
            document.querySelectorAll('.expanded-controls').forEach(el => {
                el.style.display = state.expandedBet ? 'block' : 'none';
            });
        });
        
        document.getElementById('strategy').addEventListener('change', () => {
            const strategy = document.getElementById('strategy').value;
            const info = document.getElementById('strategyInfo');
            const desc = {
                random: 'Случайный выбор каждый тираж',
                frequency: 'Выбирает самые частые числа из истории',
                cold: 'Выбирает самые редкие (холодные) числа',
                mixed: 'Комбинирует горячие и случайные'
            };
            info.textContent = desc[strategy];
        });

        document.getElementById('rngSource').addEventListener('change', () => {
            const src = document.getElementById('rngSource').value;
            const info = document.getElementById('rngInfo');
            const desc = {
                math: 'Math.random() — стандартный PRNG',
                crypto: 'crypto.getRandomValues() — криптографически стойкий',
                time: 'Time-seed xorshift — детерминированный',
                hybrid: 'Комбинация Crypto + Time',
                random: 'Случайный ГСЧ каждый тираж — сюрприз!'
            };
            info.textContent = desc[src];
        });

        document.getElementById('loadRealBtn').addEventListener('click', function() {
            RealDrawsComparison.render();
        });
    });
}

// ==================== REAL DRAWS COMPARISON ====================
const RealDrawsComparison = {
    fileMap: {
        '6x45': 'real-draws-6x45.json',
        '5x36': 'real-draws-5x36.json',
        '7x49': 'real-draws-7x49.json',
        '4x20': 'real-draws-4x20.json',
        'oxota': 'real-draws-okhota.json',
        'rapido': 'real-draws-rapido.json'
    },

    extractNumbers(gameKey, draw) {
        switch (gameKey) {
            case '6x45':
            case '5x36':
            case '7x49':
            case 'rapido':
                return draw.numbers;
            case '4x20':
            case 'oxota':
                return draw.field1.concat(draw.field2);
            default:
                return [];
        }
    },

    computeRealFrequency(draws, gameKey) {
        var freq = {};
        draws.forEach(function(draw) {
            var nums = RealDrawsComparison.extractNumbers(gameKey, draw);
            nums.forEach(function(n) {
                freq[n] = (freq[n] || 0) + 1;
            });
        });
        return freq;
    },

    getMaxNumber(gameKey) {
        var game = gameDefinitions[gameKey];
        if (game.fields) {
            var max = 0;
            game.fields.forEach(function(f) { if (f.max > max) max = f.max; });
            return max;
        }
        return 0;
    },

    renderGameComparison(gameKey, realFreq, realDrawsCount) {
        var game = gameDefinitions[gameKey];
        var simFreq = state.frequency[gameKey] || {};
        var simTotal = (state.stats[gameKey] && state.stats[gameKey].total) ? state.stats[gameKey].total : 0;

        var allNumbers = new Set();
        Object.keys(realFreq).forEach(function(n) { allNumbers.add(parseInt(n)); });
        Object.keys(simFreq).forEach(function(n) { allNumbers.add(parseInt(n)); });

        if (allNumbers.size === 0) return '';

        var sortedNums = Array.from(allNumbers).sort(function(a, b) { return a - b; });

        var maxRealCount = 0;
        var maxSimCount = 0;
        sortedNums.forEach(function(n) {
            if ((realFreq[n] || 0) > maxRealCount) maxRealCount = realFreq[n];
            if ((simFreq[n] || 0) > maxSimCount) maxSimCount = simFreq[n];
        });

        var html = '<div class="rc-game-block">';
        html += '<div class="rc-game-header">' + game.name + '</div>';
        html += '<div class="rc-game-meta">Реальных тиражей: ' + realDrawsCount;
        if (simTotal > 0) {
            html += ' | Симуляций: ' + simTotal;
        }
        html += '</div>';

        html += '<table class="rc-table">';
        html += '<thead><tr>';
        html += '<th>Число</th><th>Реал</th><th>%</th>';
        if (simTotal > 0) {
            html += '<th>Сим</th><th>%</th><th class="rc-bar-col">Сравнение</th>';
        }
        html += '</tr></thead>';
        html += '<tbody>';

        sortedNums.forEach(function(n, idx) {
            var realCount = realFreq[n] || 0;
            var realPct = realDrawsCount > 0 ? (realCount / realDrawsCount * 100).toFixed(1) : '0.0';
            var simCount = simFreq[n] || 0;
            var simPct = simTotal > 0 ? (simCount / simTotal * 100).toFixed(1) : '0.0';

            html += '<tr class="' + (idx % 2 === 0 ? 'rc-even' : 'rc-odd') + '">';
            html += '<td class="rc-num">' + n + '</td>';
            html += '<td>' + realCount + '</td>';
            html += '<td>' + realPct + '%</td>';

            if (simTotal > 0) {
                html += '<td>' + simCount + '</td>';
                html += '<td>' + simPct + '%</td>';
                html += '<td class="rc-bar-cell">';
                var realBarW = maxRealCount > 0 ? (realCount / maxRealCount * 100) : 0;
                var simBarW = maxSimCount > 0 ? (simCount / maxSimCount * 100) : 0;
                html += '<div class="rc-bar-container">';
                html += '<div class="rc-bar rc-bar-real" style="width:' + realBarW + '%"></div>';
                html += '<div class="rc-bar rc-bar-sim" style="width:' + simBarW + '%"></div>';
                html += '</div>';
                html += '</td>';
            }

            html += '</tr>';
        });

        html += '</tbody></table>';
        html += '</div>';
        return html;
    },

    async render() {
        var box = document.getElementById('realComparisonBox');
        if (!box) return;

        box.innerHTML = '<div class="rc-loading">Загрузка данных...</div>';

        var self = this;
        var gameKeys = Object.keys(this.fileMap);
        var results = {};
        var errors = [];

        var fetches = gameKeys.map(function(gameKey) {
            return fetch(self.fileMap[gameKey])
                .then(function(resp) {
                    if (!resp.ok) throw new Error('HTTP ' + resp.status);
                    return resp.json();
                })
                .then(function(data) {
                    results[gameKey] = data;
                })
                .catch(function(e) {
                    errors.push(gameDefinitions[gameKey].name + ': ' + e.message);
                });
        });

        await Promise.all(fetches);

        var html = '';

        if (errors.length > 0) {
            html += '<div class="rc-errors">Не удалось загрузить: ' + errors.join(', ') + '</div>';
        }

        var gamesWithData = gameKeys.filter(function(k) { return results[k]; });

        if (gamesWithData.length === 0) {
            html += '<div class="rc-empty">Данные не загружены</div>';
        } else {
            gamesWithData.forEach(function(gameKey) {
                var data = results[gameKey];
                var realFreq = self.computeRealFrequency(data.draws, gameKey);
                html += self.renderGameComparison(gameKey, realFreq, data.draws.length);
            });
        }

        var gamesWithout = gameKeys.filter(function(k) { return !results[k]; });
        if (gamesWithout.length > 0) {
            html += '<div class="rc-no-data">Нет данных для: ' + gamesWithout.map(function(k) { return gameDefinitions[k].name; }).join(', ') + '</div>';
        }

        box.innerHTML = html;
    }
};

// Экспорт для Node.js
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { gameDefinitions, GameEngine, Strategies, generateUniqueNumbers, combinations, state };
}
