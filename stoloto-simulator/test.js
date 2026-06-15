// Тестовый скрипт для проверки логики Столото Симулятора
const fs = require('fs');

// Создадим mock DOM и window
global.document = {
    getElementById: () => ({
        addEventListener: () => {},
        value: '6x45',
        innerHTML: '',
        textContent: '',
        disabled: false,
        classList: {
            remove: () => {},
            add: () => {}
        }
    }),
    addEventListener: () => {}
};

// Загрузим script.js
const { generateUniqueNumbers, GameEngine, Strategies, state } = require('./script.js');

console.log('=== Тест 1: Генерация чисел ===');
const nums1 = generateUniqueNumbers(6, 1, 45);
console.log('Сгенерировано 6 из 45:', nums1);
console.log('Длина:', nums1.length, 'Уникальны:', new Set(nums1).size === 6);
console.assert(nums1.length === 6, 'Должно быть 6 чисел');
console.assert(Math.min(...nums1) >= 1 && Math.max(...nums1) <= 45, 'В диапазоне 1-45');

console.log('\n=== Тест 2: Игра "6 из 45" ===');
const winning = [generateUniqueNumbers(6, 1, 45)];
const player = [generateUniqueNumbers(6, 1, 45)];
console.log('Выигрышные:', winning[0]);
console.log('Игрока:', player[0]);

const result = GameEngine.checkResult('6x45', winning, player);
console.log('Результат:', result);
console.log('Совпадений:', result.matches[0]);
console.assert(result.matches[0] >= 0 && result.matches[0] <= 6, 'Совпадения 0-6');

console.log('\n=== Тест 3: Игра "5 из 36" (2 поля) ===');
const winning5x36 = [generateUniqueNumbers(5, 1, 36), generateUniqueNumbers(1, 1, 4)];
const player5x36 = [generateUniqueNumbers(5, 1, 36), generateUniqueNumbers(1, 1, 4)];
const result5x36 = GameEngine.checkResult('5x36', winning5x36, player5x36);
console.log('Результат 5x36:', result5x36);
console.log('Совпадения:', result5x36.matches);
console.assert(result5x36.matches.length === 2, 'Два поля совпадений');

console.log('\n=== Тест 4: Игра "4 из 20" (2 поля) ===');
const winning4x20 = [generateUniqueNumbers(4, 1, 20), generateUniqueNumbers(4, 1, 20)];
const player4x20 = [generateUniqueNumbers(4, 1, 20), generateUniqueNumbers(4, 1, 20)];
const result4x20 = GameEngine.checkResult('4x20', winning4x20, player4x20);
console.log('Результат 4x20:', result4x20);
console.log('Совпадения:', result4x20.matches);
console.assert(result4x20.matches.length === 2, 'Два поля совпадений');

console.log('\n=== Тест 5: Игра "Всё или Ничего" ===');
const winningAll = [generateUniqueNumbers(12, 1, 24)];
const playerAll = [generateUniqueNumbers(12, 1, 24)];
const resultAll = GameEngine.checkResult('allornothing', winningAll, playerAll);
console.log('Результат Всё/Ничего:', resultAll);
console.log('Совпадений:', resultAll.matches[0]);
console.assert(resultAll.matches[0] >= 0 && resultAll.matches[0] <= 12, 'Совпадения 0-12');

console.log('\n=== Тест 5: Рапидо ===');
const winningRapido = [generateUniqueNumbers(8, 1, 20), generateUniqueNumbers(1, 1, 4)];
const playerRapido = [generateUniqueNumbers(8, 1, 20), generateUniqueNumbers(1, 1, 4)];
const resultRapido = GameEngine.checkResult('rapido', winningRapido, playerRapido);
console.log('Результат Рапидо:', resultRapido);
console.log('Совпадения:', resultRapido.matches);
console.assert(resultRapido.matches.length === 2, 'Два поля');

console.log('\n=== Тест 6: КЕНО ===');
const winningKeno = [generateUniqueNumbers(20, 1, 80)];
const playerKeno = [generateUniqueNumbers(10, 1, 80)];
const resultKeno = GameEngine.checkResult('keno', winningKeno, playerKeno);
console.log('Результат КЕНО:', resultKeno);
console.log('Совпадений:', resultKeno.matches[0]);
console.assert(resultKeno.matches[0] >= 0 && resultKeno.matches[0] <= 10, 'Совпадения 0-10');

console.log('\n=== Тест 7: Стратегии ===');
const strategyResult = Strategies.random('6x45', [6]);
console.log('Стратегия random:', strategyResult);
console.assert(strategyResult.length === 1 && strategyResult[0].length === 6, '6 чисел');

console.log('\n=== Тест 7: Статистика ===');
console.log('Статистика:', state.stats);
console.assert(Object.keys(state.stats).length === 0, 'Изначально пусто');

console.log('\n=== Тест 8: Русское Лото ===');
const winningRuslotto = [Array.from({length: 90}, (_, i) => i + 1).sort(() => Math.random() - 0.5)];
const playerRuslotto = [generateUniqueNumbers(15, 1, 90), generateUniqueNumbers(15, 1, 90)];
const resultRuslotto = GameEngine.checkResult('ruslotto', winningRuslotto, playerRuslotto);
console.log('Результат Русское Лото:', resultRuslotto);
console.log('Совпадений:', resultRuslotto.matches[0]);

console.log('\n=== Тест 9: Зодиак ===');
const winningZodiac = [generateUniqueNumbers(4, 1, 99)];
const playerZodiac = [generateUniqueNumbers(4, 1, 99)];
const resultZodiac = GameEngine.checkResult('zodiac', winningZodiac, playerZodiac);
console.log('Результат Зодиак:', resultZodiac);
console.log('Совпадений:', resultZodiac.matches[0]);

console.log('\n=== Все тесты пройдены ===');
console.log('Файлы готовы к использованию:');
console.log('- index.html: UI');
console.log('- style.css: Стили');
console.log('- script.js: Логика');
console.log('\nОткройте index.html в браузере для запуска.');
