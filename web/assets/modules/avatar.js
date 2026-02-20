export function createAvatarRenderer({ state, seededRandom, hashText }) {
    function normalizeAvatarProvider(provider) {
      const raw = String(provider || '').trim().toLowerCase();
      return ['claude', 'codex', 'gemini'].includes(raw) ? raw : 'system';
    }

    function avatarVariantInfo(roleId, provider, scope, variantCount) {
      const key = normalizeAvatarProvider(provider);
      const role = String(roleId || 'system').trim() || 'system';
      const scopeKey = String(scope || state.theme || 'pixel').trim().toLowerCase();
      const count = Math.max(1, Number(variantCount || 1));
      const cacheKey = `${scopeKey}|${key}|${role}`;
      const cached = state.avatarVariantCache.get(cacheKey);
      if (cached && Number(cached.variantCount) === count) {
        return cached;
      }
      const seeded = seededRandom(hashText(`${cacheKey}|${state.avatarSessionSalt}`));
      const info = {
        variantCount: count,
        variant: Math.floor(seeded() * count),
        noise: Math.floor(seeded() * 1000000000),
      };
      state.avatarVariantCache.set(cacheKey, info);
      return info;
    }

    function avatarPalette(provider, rng) {
      const key = String(provider || '').toLowerCase();
      const isPixelTheme = state.theme === 'pixel';
      const skinPairs = [
        ['#f6d2b7', '#d9a88b'],
        ['#deb38c', '#bf8c68'],
        ['#c98b66', '#a76c49'],
        ['#9b6546', '#7f4f36'],
      ];
      const hairByProvider = isPixelTheme
        ? (key === 'codex'
          ? ['#d8e7ff', '#b8c8df', '#95a8c9', '#4f6076']
          : key === 'gemini'
            ? ['#ffe59a', '#ffd166', '#e6b63f', '#8f6a1e']
            : ['#f1f1f1', '#d7d7d7', '#aaaaaa', '#5f5f5f'])
        : key === 'claude'
          ? ['#f1f1f1', '#d7d7d7', '#aaaaaa', '#5f5f5f']
          : key === 'codex'
            ? ['#d8e7ff', '#b8c8df', '#8fa0bc', '#4f6076']
            : key === 'gemini'
              ? ['#ffe59a', '#ffd166', '#e6b63f', '#8f6a1e']
              : ['#e6d8b5', '#bfbfbf', '#8a8a8a', '#5a5a5a'];
      const shirtByProvider = isPixelTheme
        ? (key === 'codex'
          ? [['#345587', '#263f67'], ['#2e4064', '#253453'], ['#5f5f75', '#48485d']]
          : key === 'gemini'
            ? [['#8a5a20', '#684214'], ['#5d4f2e', '#463c22'], ['#4f4b68', '#3d3a53']]
            : [['#5a5a5a', '#454545'], ['#61677a', '#4a5060'], ['#6a4444', '#533434']])
        : key === 'claude'
          ? [['#2f6f49', '#24533a'], ['#315f8f', '#264970'], ['#6d5a2e', '#584821']]
          : key === 'codex'
            ? [['#345587', '#263f67'], ['#2d6a71', '#235258'], ['#63558b', '#4d416d']]
            : key === 'gemini'
              ? [['#8a5a20', '#684214'], ['#2d5d68', '#23474f'], ['#57456f', '#423455']]
              : [['#5a5a5a', '#454545'], ['#3e5d3f', '#304932'], ['#6a4444', '#533434']];
      const bgByProvider = isPixelTheme
        ? (key === 'codex'
          ? [['#0d1624', '#142032'], ['#11182a', '#182944']]
          : key === 'gemini'
            ? [['#1f160a', '#2a1d0e'], ['#1a1410', '#262018']]
            : [['#141414', '#1c1c1c'], ['#101317', '#181d22']])
        : key === 'claude'
          ? [['#0f1a13', '#112319'], ['#11161f', '#15233a']]
          : key === 'codex'
            ? [['#0d1624', '#142032'], ['#11182a', '#182944']]
            : key === 'gemini'
              ? [['#1f160a', '#2a1d0e'], ['#1a1410', '#262018']]
              : [['#141414', '#1c1c1c'], ['#101317', '#181d22']];
      const skinChoice = skinPairs[Math.floor(rng() * skinPairs.length)];
      const shirtChoice = shirtByProvider[Math.floor(rng() * shirtByProvider.length)];
      const bgChoice = bgByProvider[Math.floor(rng() * bgByProvider.length)];
      return {
        skin: skinChoice[0],
        skinShade: skinChoice[1],
        hair: hairByProvider[Math.floor(rng() * hairByProvider.length)],
        shirt: shirtChoice[0],
        shirtShade: shirtChoice[1],
        eyeWhite: '#f5f5f5',
        pupil: '#121212',
        lip: '#8a4b4b',
        outline: '#050505',
        bg: bgChoice[0],
        bgShade: bgChoice[1],
        accent: isPixelTheme
          ? (key === 'codex' ? '#9ec9ff' : key === 'gemini' ? '#ffd166' : '#d7dce6')
          : (key === 'claude' ? '#9fffd0' : key === 'codex' ? '#9ec9ff' : key === 'gemini' ? '#ffd166' : '#f0d79a'),
      };
    }

    function generateAvatarSvg(roleId, provider) {
      if (state.theme === 'pixel-sw') {
        return generateAvatarSvgStarWars(roleId, provider);
      }
      if (state.theme === 'pixel-sg') {
        return generateAvatarSvgThreeKingdoms(roleId, provider);
      }
      if (state.theme === 'pixel') {
        return generateAvatarSvgPixel(roleId, provider);
      }
      const seed = `${provider}|${roleId}`;
      const rng = seededRandom(hashText(seed));
      const palette = avatarPalette(provider, rng);
      const size = 24;
      const grid = Array.from({ length: size }, () => Array(size).fill(null));

      function px(x, y, color) {
        if (x < 0 || y < 0 || x >= size || y >= size) return;
        grid[y][x] = color;
      }

      function fillRect(x1, y1, x2, y2, color) {
        for (let y = y1; y <= y2; y += 1) {
          for (let x = x1; x <= x2; x += 1) {
            px(x, y, color);
          }
        }
      }

      function strokeRect(x1, y1, x2, y2, color) {
        for (let x = x1; x <= x2; x += 1) {
          px(x, y1, color);
          px(x, y2, color);
        }
        for (let y = y1; y <= y2; y += 1) {
          px(x1, y, color);
          px(x2, y, color);
        }
      }

      fillRect(0, 0, 23, 23, palette.bg);
      fillRect(0, 16, 23, 23, palette.bgShade);
      for (let y = 0; y < size; y += 2) {
        for (let x = 0; x < size; x += 2) {
          if (((x + y) / 2 + Math.floor(rng() * 5)) % 5 === 0) {
            px(x, y, palette.bgShade);
          }
        }
      }

      fillRect(3, 17, 20, 23, palette.shirt);
      fillRect(5, 19, 18, 23, palette.shirtShade);
      fillRect(10, 16, 13, 18, palette.skin);
      fillRect(10, 18, 13, 18, palette.skinShade);
      fillRect(8, 17, 9, 18, palette.shirtShade);
      fillRect(14, 17, 15, 18, palette.shirtShade);

      fillRect(6, 5, 17, 16, palette.skin);
      fillRect(5, 7, 5, 12, palette.skin);
      fillRect(18, 7, 18, 12, palette.skin);
      fillRect(7, 14, 16, 16, palette.skinShade);
      strokeRect(6, 5, 17, 16, palette.outline);
      px(6, 16, palette.skinShade);
      px(17, 16, palette.skinShade);
      fillRect(5, 8, 5, 11, palette.skinShade);
      fillRect(18, 8, 18, 11, palette.skinShade);

      const hairStyle = Math.floor(rng() * 4);
      if (hairStyle === 0) {
        fillRect(5, 2, 18, 6, palette.hair);
        fillRect(5, 7, 6, 9, palette.hair);
        fillRect(17, 7, 18, 9, palette.hair);
      } else if (hairStyle === 1) {
        fillRect(4, 2, 19, 5, palette.hair);
        fillRect(4, 6, 4, 12, palette.hair);
        fillRect(19, 6, 19, 9, palette.hair);
        fillRect(8, 6, 14, 6, palette.hair);
      } else if (hairStyle === 2) {
        fillRect(5, 2, 18, 4, palette.hair);
        fillRect(5, 5, 7, 9, palette.hair);
        fillRect(16, 5, 18, 9, palette.hair);
        fillRect(8, 5, 15, 5, palette.hair);
      } else {
        fillRect(6, 2, 17, 4, palette.hair);
        fillRect(6, 5, 6, 10, palette.hair);
        fillRect(17, 5, 17, 10, palette.hair);
        fillRect(8, 5, 15, 5, palette.hair);
        fillRect(10, 1, 13, 1, palette.hair);
      }

      fillRect(8, 8, 10, 8, palette.outline);
      fillRect(13, 8, 15, 8, palette.outline);
      fillRect(8, 9, 10, 10, palette.eyeWhite);
      fillRect(13, 9, 15, 10, palette.eyeWhite);

      const eyeShift = rng() > 0.5 ? 0 : 1;
      px(9 + eyeShift, 9, palette.pupil);
      px(14 + eyeShift, 9, palette.pupil);
      if (rng() > 0.6) {
        px(8, 10, palette.pupil);
        px(15, 10, palette.pupil);
      }

      fillRect(11, 10, 12, 12, palette.skinShade);
      px(11, 13, palette.skinShade);
      px(12, 13, palette.skinShade);

      const mouthStyle = Math.floor(rng() * 4);
      if (mouthStyle === 0) {
        fillRect(9, 14, 14, 14, palette.lip);
      } else if (mouthStyle === 1) {
        fillRect(9, 14, 10, 14, palette.lip);
        fillRect(11, 15, 12, 15, palette.lip);
        fillRect(13, 14, 14, 14, palette.lip);
      } else if (mouthStyle === 2) {
        fillRect(9, 15, 14, 15, palette.lip);
      } else {
        fillRect(10, 14, 13, 14, palette.lip);
      }

      if (rng() > 0.62) {
        fillRect(7, 9, 10, 10, palette.outline);
        fillRect(13, 9, 16, 10, palette.outline);
        fillRect(11, 9, 12, 9, palette.outline);
      }
      if (rng() > 0.76) {
        fillRect(8, 15, 15, 16, palette.skinShade);
        px(10, 16, palette.outline);
        px(13, 16, palette.outline);
      }
      if (rng() > 0.7) {
        fillRect(2, 10, 4, 11, palette.accent);
        fillRect(19, 10, 21, 11, palette.accent);
        fillRect(3, 12, 3, 13, palette.outline);
        fillRect(20, 12, 20, 13, palette.outline);
      }

      strokeRect(3, 17, 20, 23, palette.outline);
      fillRect(9, 19, 14, 19, palette.outline);

      const rects = [`<rect width="${size}" height="${size}" fill="${palette.bg}"></rect>`];
      for (let y = 0; y < size; y += 1) {
        for (let x = 0; x < size; x += 1) {
          const color = grid[y][x];
          if (!color) continue;
          rects.push(`<rect x="${x}" y="${y}" width="1" height="1" fill="${color}"></rect>`);
        }
      }
      return `<svg viewBox="0 0 ${size} ${size}" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">${rects.join('')}</svg>`;
    }

    function generateAvatarSvgPixel(roleId, provider) {
      const key = normalizeAvatarProvider(provider);
      const style = avatarVariantInfo(roleId, key, 'pixel', 5);
      const variant = Number(style.variant || 0);
      const seed = `px-modern-human|${key}|${roleId}|${style.noise}`;
      const rng = seededRandom(hashText(seed));
      const size = 24;
      const grid = Array.from({ length: size }, () => Array(size).fill(null));

      const palettes = {
        system: {
          bg: '#0a0f16', bgShade: '#111a27', line: '#1f3147',
          skin: '#d6dcee', skinShade: '#b4bfd8',
          hair: '#b9c7e4', hairShade: '#8da0c5',
          eyeWhite: '#edf3ff', pupil: '#111b2c',
          cloth: '#3f526f', clothShade: '#2f4058',
          accent: '#9ec4ff', lip: '#8e9ab1',
        },
        claude: {
          bg: '#14100e', bgShade: '#1e1714', line: '#34261f',
          skin: '#efcaab', skinShade: '#d9aa85',
          hair: '#ebe7de', hairShade: '#ccc6bd',
          eyeWhite: '#faf6ef', pupil: '#1b140f',
          cloth: '#625a70', clothShade: '#4b4458',
          accent: '#d8c3a1', lip: '#9b6f62',
        },
        codex: {
          bg: '#0b121f', bgShade: '#121d32', line: '#26426a',
          skin: '#dcc2a3', skinShade: '#c19c78',
          hair: '#93abd6', hairShade: '#6d85b2',
          eyeWhite: '#ecf3ff', pupil: '#11233f',
          cloth: '#345383', clothShade: '#274064',
          accent: '#9fd2ff', lip: '#8f6f58',
        },
        gemini: {
          bg: '#171208', bgShade: '#231b0d', line: '#3d2f12',
          skin: '#e8c8a1', skinShade: '#cba073',
          hair: '#e7bc67', hairShade: '#b68d41',
          eyeWhite: '#fff4db', pupil: '#2b1f10',
          cloth: '#675133', clothShade: '#4c3b26',
          accent: '#ffd978', lip: '#9f744f',
        },
      };
      const p = palettes[key];

      function px(x, y, color) {
        if (x < 0 || y < 0 || x >= size || y >= size) return;
        grid[y][x] = color;
      }

      function fillRect(x1, y1, x2, y2, color) {
        for (let y = y1; y <= y2; y += 1) {
          for (let x = x1; x <= x2; x += 1) {
            px(x, y, color);
          }
        }
      }

      function strokeRect(x1, y1, x2, y2, color) {
        for (let x = x1; x <= x2; x += 1) {
          px(x, y1, color);
          px(x, y2, color);
        }
        for (let y = y1; y <= y2; y += 1) {
          px(x1, y, color);
          px(x2, y, color);
        }
      }

      function drawEyes() {
        fillRect(9, 9, 10, 10, p.eyeWhite);
        fillRect(13, 9, 14, 10, p.eyeWhite);
        px(10, 10, p.pupil);
        px(14, 10, p.pupil);
      }

      fillRect(0, 0, 23, 23, p.bg);
      fillRect(0, 16, 23, 23, p.bgShade);
      for (let y = 0; y < size; y += 2) {
        for (let x = 0; x < size; x += 2) {
          if (rng() > 0.8) px(x, y, p.bgShade);
        }
      }

      for (let y = 0; y < size; y += 1) {
        px(0, y, p.line);
        px(size - 1, y, p.line);
      }
      for (let x = 0; x < size; x += 1) {
        px(x, 0, p.line);
        px(x, size - 1, p.line);
      }

      // Modern human portrait base.
      fillRect(6, 16, 17, 23, p.cloth);
      fillRect(8, 18, 15, 23, p.clothShade);
      fillRect(10, 14, 13, 16, p.skin);
      fillRect(10, 15, 13, 16, p.skinShade);

      fillRect(7, 6, 16, 15, p.skin);
      fillRect(8, 11, 15, 15, p.skinShade);
      fillRect(6, 8, 6, 12, p.skin);
      fillRect(17, 8, 17, 12, p.skin);
      fillRect(6, 9, 6, 12, p.skinShade);
      fillRect(17, 9, 17, 12, p.skinShade);

      drawEyes();
      fillRect(11, 10, 12, 12, p.skinShade);
      fillRect(10, 13, 13, 13, p.lip);
      strokeRect(7, 6, 16, 15, p.line);

      if (key === 'system') {
        // Modern ops headset.
        fillRect(6, 4, 17, 7, p.hair);
        fillRect(7, 8, 8, 11, p.hairShade);
        fillRect(15, 8, 16, 10, p.hairShade);
        fillRect(18, 8, 19, 12, p.accent);
        fillRect(18, 12, 20, 12, p.accent);
        fillRect(10, 18, 13, 18, p.accent);
      } else if (key === 'claude') {
        // Side-part + scarf.
        fillRect(6, 4, 17, 7, p.hair);
        fillRect(6, 8, 7, 13, p.hairShade);
        fillRect(14, 8, 17, 9, p.hairShade);
        fillRect(9, 18, 14, 18, p.accent);
        fillRect(11, 19, 12, 21, p.accent);
      } else if (key === 'codex') {
        // Hoodie + sleek visor.
        fillRect(5, 5, 18, 9, p.hairShade);
        fillRect(5, 10, 6, 14, p.hairShade);
        fillRect(17, 10, 18, 14, p.hairShade);
        fillRect(8, 8, 15, 9, p.accent);
        fillRect(10, 19, 13, 20, p.accent);
      } else {
        // Glasses + small forehead clip.
        fillRect(6, 4, 17, 7, p.hair);
        fillRect(7, 8, 16, 8, p.hairShade);
        strokeRect(8, 8, 10, 11, p.accent);
        strokeRect(13, 8, 15, 11, p.accent);
        fillRect(11, 9, 12, 9, p.accent);
        fillRect(11, 4, 12, 4, p.accent);
      }

      if (variant === 1) {
        // Variant: cleaner glasses profile.
        strokeRect(8, 9, 10, 10, p.accent);
        strokeRect(13, 9, 15, 10, p.accent);
        fillRect(11, 9, 12, 9, p.accent);
      } else if (variant === 2) {
        // Variant: stronger brow and jaw.
        fillRect(8, 7, 10, 7, p.line);
        fillRect(13, 7, 15, 7, p.line);
        fillRect(9, 14, 14, 15, p.skinShade);
      } else if (variant === 3) {
        // Variant: side comms headset.
        fillRect(5, 9, 6, 12, p.accent);
        fillRect(17, 9, 18, 12, p.accent);
        fillRect(18, 12, 20, 12, p.accent);
      } else if (variant === 4) {
        // Variant: chest badge + collar.
        fillRect(9, 18, 14, 18, p.accent);
        fillRect(10, 19, 13, 20, p.accent);
      }

      strokeRect(6, 16, 17, 23, p.line);

      const rects = [`<rect width="${size}" height="${size}" fill="${p.bg}"></rect>`];
      for (let y = 0; y < size; y += 1) {
        for (let x = 0; x < size; x += 1) {
          const color = grid[y][x];
          if (!color) continue;
          rects.push(`<rect x="${x}" y="${y}" width="1" height="1" fill="${color}"></rect>`);
        }
      }
      return `<svg viewBox="0 0 ${size} ${size}" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">${rects.join('')}</svg>`;
    }

    function generateAvatarSvgStarWars(roleId, provider) {
      const key = normalizeAvatarProvider(provider);
      const style = avatarVariantInfo(roleId, key, 'pixel-sw', 5);
      const variant = Number(style.variant || 0);
      const seed = `sw|${key}|${roleId}|${style.noise}`;
      const rng = seededRandom(hashText(seed));
      const size = 24;
      const grid = Array.from({ length: size }, () => Array(size).fill(null));

      const palettes = {
        system: {
          bg: '#050812', bgShade: '#0b1230', outline: '#010102',
          skin: '#d2dcff', skinShade: '#a6b3d8', cloth: '#3a4e7f', clothShade: '#2b3b62',
          armor: '#7a8ebc', armorShade: '#5d6f99', visor: '#0a0f1e',
          saber: '#7cc7ff', saberCore: '#e0f5ff', accent: '#ffe081',
          starA: '#dfe7ff', starB: '#ffe8a8', starC: '#95c7ff',
        },
        claude: {
          bg: '#060912', bgShade: '#0f1530', outline: '#010102',
          skin: '#f1c9a3', skinShade: '#d8a77f', cloth: '#7b5d38', clothShade: '#5b4329',
          armor: '#3a4f66', armorShade: '#2a3b4f', visor: '#0a0d14',
          saber: '#59b8ff', saberCore: '#e5f5ff', accent: '#ffe081',
          starA: '#dfe7ff', starB: '#ffe8a8', starC: '#95c7ff',
        },
        codex: {
          bg: '#05070f', bgShade: '#1a1022', outline: '#010102',
          skin: '#d8dfe8', skinShade: '#b5c0cf', cloth: '#2f3448', clothShade: '#222637',
          armor: '#e6ebf2', armorShade: '#bfc9d8', visor: '#131822',
          saber: '#ff6262', saberCore: '#ffdada', accent: '#9fb4ff',
          starA: '#dfe7ff', starB: '#ffe8a8', starC: '#95c7ff',
        },
        gemini: {
          bg: '#09070f', bgShade: '#161327', outline: '#010102',
          skin: '#d2b26a', skinShade: '#a1844c', cloth: '#5f4b2a', clothShade: '#45361d',
          armor: '#b3bcc9', armorShade: '#8893a3', visor: '#13141b',
          saber: '#ffc857', saberCore: '#fff2cf', accent: '#72b9ff',
          starA: '#dfe7ff', starB: '#ffe8a8', starC: '#95c7ff',
        },
      };
      const p = palettes[key];

      function px(x, y, color) {
        if (x < 0 || y < 0 || x >= size || y >= size) return;
        grid[y][x] = color;
      }

      function fillRect(x1, y1, x2, y2, color) {
        for (let y = y1; y <= y2; y += 1) {
          for (let x = x1; x <= x2; x += 1) {
            px(x, y, color);
          }
        }
      }

      function strokeRect(x1, y1, x2, y2, color) {
        for (let x = x1; x <= x2; x += 1) {
          px(x, y1, color);
          px(x, y2, color);
        }
        for (let y = y1; y <= y2; y += 1) {
          px(x1, y, color);
          px(x2, y, color);
        }
      }

      function drawVerticalSaber(x, y1, y2, glowColor, coreColor) {
        for (let y = y1; y <= y2; y += 1) {
          px(x, y, coreColor);
          px(x - 1, y, glowColor);
          px(x + 1, y, glowColor);
        }
        fillRect(x - 1, y2 + 1, x + 1, y2 + 2, p.outline);
      }

      function drawEyes(x1, x2, y) {
        fillRect(x1, y, x1 + 1, y, p.visor);
        fillRect(x2, y, x2 + 1, y, p.visor);
      }

      fillRect(0, 0, 23, 23, p.bg);
      fillRect(0, 16, 23, 23, p.bgShade);
      for (let i = 0; i < 34; i += 1) {
        const sx = Math.floor(rng() * 24);
        const sy = Math.floor(rng() * 24);
        const starColor = (i % 3 === 0) ? p.starA : (i % 3 === 1 ? p.starB : p.starC);
        if (rng() > 0.55) {
          px(sx, sy, starColor);
        }
      }

      if (key === 'claude') {
        // Hooded Jedi-like role (blue saber)
        fillRect(6, 15, 17, 23, p.cloth);
        fillRect(8, 17, 15, 23, p.clothShade);
        fillRect(8, 6, 15, 14, p.skin);
        fillRect(9, 11, 14, 14, p.skinShade);
        fillRect(6, 5, 17, 8, p.cloth);
        fillRect(7, 7, 8, 12, p.cloth);
        fillRect(15, 7, 16, 12, p.cloth);
        fillRect(9, 4, 14, 5, p.clothShade);
        drawEyes(9, 13, 9);
        fillRect(10, 12, 13, 12, p.visor);
        strokeRect(6, 5, 17, 23, p.outline);
        drawVerticalSaber(20, 8, 20, p.saber, p.saberCore);
      } else if (key === 'codex') {
        // Trooper-like role (red saber)
        fillRect(7, 15, 16, 23, p.armor);
        fillRect(8, 17, 15, 23, p.armorShade);
        fillRect(7, 6, 16, 14, p.armor);
        fillRect(8, 7, 15, 13, p.skin);
        fillRect(8, 8, 15, 10, p.visor);
        fillRect(9, 11, 14, 13, p.skinShade);
        fillRect(9, 6, 14, 6, p.armorShade);
        fillRect(7, 11, 7, 12, p.armorShade);
        fillRect(16, 11, 16, 12, p.armorShade);
        fillRect(10, 18, 13, 19, p.visor);
        strokeRect(7, 6, 16, 23, p.outline);
        drawVerticalSaber(3, 8, 20, p.saber, p.saberCore);
      } else if (key === 'gemini') {
        // Droid-like role with protocol colors
        fillRect(8, 15, 15, 23, p.cloth);
        fillRect(9, 17, 14, 23, p.clothShade);
        fillRect(8, 6, 15, 14, p.armor);
        fillRect(9, 7, 14, 12, p.skin);
        fillRect(10, 8, 11, 8, p.visor);
        fillRect(12, 8, 13, 8, p.visor);
        fillRect(10, 10, 13, 10, p.accent);
        fillRect(9, 13, 14, 14, p.armorShade);
        px(11, 4, p.accent);
        px(12, 4, p.accent);
        fillRect(11, 5, 12, 5, p.armorShade);
        strokeRect(8, 6, 15, 23, p.outline);
      } else {
        // System command/hologram role
        fillRect(7, 15, 16, 23, p.cloth);
        fillRect(8, 17, 15, 23, p.clothShade);
        fillRect(8, 6, 15, 14, p.skin);
        fillRect(9, 7, 14, 13, p.skinShade);
        drawEyes(9, 13, 9);
        fillRect(10, 12, 13, 12, p.visor);
        fillRect(10, 18, 13, 19, p.accent);
        strokeRect(7, 6, 16, 23, p.outline);
        drawVerticalSaber(19, 9, 20, p.saber, p.saberCore);
      }

      if (variant === 1) {
        // Variant: shoulder pauldron.
        fillRect(5, 13, 8, 15, p.armor);
        fillRect(15, 13, 18, 15, p.armorShade);
      } else if (variant === 2) {
        // Variant: tactical chest panel.
        fillRect(9, 17, 14, 19, p.accent);
        fillRect(10, 18, 13, 18, p.visor);
      } else if (variant === 3) {
        // Variant: comm antenna.
        fillRect(4, 5, 4, 13, p.armorShade);
        fillRect(3, 5, 5, 6, p.accent);
      } else if (variant === 4) {
        // Variant: visor stripe and belt nodes.
        fillRect(8, 8, 15, 8, p.accent);
        px(9, 20, p.accent);
        px(12, 20, p.accent);
        px(15, 20, p.accent);
      }

      const rects = [`<rect width="${size}" height="${size}" fill="${p.bg}"></rect>`];
      for (let y = 0; y < size; y += 1) {
        for (let x = 0; x < size; x += 1) {
          const color = grid[y][x];
          if (!color) continue;
          rects.push(`<rect x="${x}" y="${y}" width="1" height="1" fill="${color}"></rect>`);
        }
      }
      return `<svg viewBox="0 0 ${size} ${size}" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">${rects.join('')}</svg>`;
    }

    function generateAvatarSvgThreeKingdoms(roleId, provider) {
      const key = normalizeAvatarProvider(provider);
      const style = avatarVariantInfo(roleId, key, 'pixel-sg', 5);
      const variant = Number(style.variant || 0);
      const seed = `sg|${key}|${roleId}|${style.noise}`;
      const rng = seededRandom(hashText(seed));
      const size = 24;
      const grid = Array.from({ length: size }, () => Array(size).fill(null));

      const palettes = {
        system: {
          bg: '#1b0f08', bgShade: '#2a130c', outline: '#050302',
          skin: '#efcdaa', skinShade: '#d7ac86',
          robe: '#6e1f1f', robeShade: '#511414',
          hat: '#d4a85f', hatShade: '#9a7335',
          eye: '#170d09', prop: '#b0533b', propShade: '#7f2f22',
          fan: '#efdec0', fanShade: '#d8c3a0',
          scroll: '#ead7af', scrollBand: '#ae7f43',
          plume: '#c74a3d', crown: '#d4a85f', crownShade: '#9a7335',
          seal: '#b94732', sealShade: '#7a291f',
          sparkA: '#f2cc92', sparkB: '#9ab4df',
        },
        claude: {
          bg: '#120f12', bgShade: '#1d1520', outline: '#050505',
          skin: '#efcfad', skinShade: '#d8ad87',
          robe: '#365f92', robeShade: '#28496f',
          hat: '#1c1c28', hatShade: '#2b2b39',
          eye: '#0f1015', prop: '#8c6c3c', propShade: '#664f2c',
          fan: '#efe7d4', fanShade: '#c9baa2',
          scroll: '#e6d2a5', scrollBand: '#a9793f',
          plume: '#d16d54', crown: '#c89c58', crownShade: '#906f38',
          seal: '#ab4b39', sealShade: '#742a21',
          sparkA: '#dfc28f', sparkB: '#9ab4df',
        },
        codex: {
          bg: '#140c08', bgShade: '#23120c', outline: '#040302',
          skin: '#e5c19f', skinShade: '#c99a75',
          robe: '#5d2f23', robeShade: '#452217',
          hat: '#6a727f', hatShade: '#4e5560',
          eye: '#130b08', prop: '#cb9d5c', propShade: '#7d5d2e',
          fan: '#ecd7b2', fanShade: '#ccb28b',
          scroll: '#e5d09f', scrollBand: '#a67332',
          plume: '#c83f3a', crown: '#c29b5c', crownShade: '#8f6f3a',
          seal: '#ad4430', sealShade: '#73261d',
          sparkA: '#e5bf83', sparkB: '#a4b8d8',
        },
        gemini: {
          bg: '#0e1116', bgShade: '#151a23', outline: '#040507',
          skin: '#e4c5a2', skinShade: '#c7a077',
          robe: '#2f4c72', robeShade: '#223954',
          hat: '#3f4f68', hatShade: '#2f3b4e',
          eye: '#0b1016', prop: '#84a7d8', propShade: '#5e79a4',
          fan: '#e8d7b6', fanShade: '#c9b28f',
          scroll: '#e8d3a2', scrollBand: '#b47d3a',
          plume: '#c66955', crown: '#c7a061', crownShade: '#8f6d3a',
          seal: '#a64b3d', sealShade: '#6d2c24',
          sparkA: '#d8bf8d', sparkB: '#9cb7e7',
        },
      };
      const p = palettes[key];

      function px(x, y, color) {
        if (x < 0 || y < 0 || x >= size || y >= size) return;
        grid[y][x] = color;
      }

      function fillRect(x1, y1, x2, y2, color) {
        for (let y = y1; y <= y2; y += 1) {
          for (let x = x1; x <= x2; x += 1) {
            px(x, y, color);
          }
        }
      }

      function strokeRect(x1, y1, x2, y2, color) {
        for (let x = x1; x <= x2; x += 1) {
          px(x, y1, color);
          px(x, y2, color);
        }
        for (let y = y1; y <= y2; y += 1) {
          px(x1, y, color);
          px(x2, y, color);
        }
      }

      function drawEyes(x1, x2, y) {
        fillRect(x1, y, x1 + 1, y, p.eye);
        fillRect(x2, y, x2 + 1, y, p.eye);
      }

      fillRect(0, 0, 23, 23, p.bg);
      fillRect(0, 16, 23, 23, p.bgShade);
      for (let i = 0; i < 30; i += 1) {
        const sx = Math.floor(rng() * 24);
        const sy = Math.floor(rng() * 24);
        const sparkle = i % 2 === 0 ? p.sparkA : p.sparkB;
        if (rng() > 0.58) {
          px(sx, sy, sparkle);
        }
      }

      if (key === 'claude') {
        // Strategist: scholar robe + feather fan.
        fillRect(6, 15, 17, 23, p.robe);
        fillRect(8, 17, 15, 23, p.robeShade);
        fillRect(8, 6, 15, 14, p.skin);
        fillRect(9, 11, 14, 14, p.skinShade);
        fillRect(7, 4, 16, 6, p.hat);
        fillRect(9, 3, 14, 3, p.hatShade);
        fillRect(6, 5, 6, 8, p.hat);
        fillRect(17, 5, 17, 8, p.hat);
        drawEyes(9, 13, 9);
        fillRect(10, 12, 13, 12, p.eye);
        strokeRect(6, 6, 17, 23, p.outline);

        fillRect(1, 10, 6, 14, p.fan);
        fillRect(2, 11, 5, 13, p.fanShade);
        fillRect(3, 11, 3, 13, p.outline);
        fillRect(4, 11, 4, 13, p.outline);
        fillRect(6, 12, 7, 12, p.prop);
        fillRect(7, 11, 7, 13, p.prop);
      } else if (key === 'codex') {
        // General: helmet + plume + halberd.
        fillRect(7, 15, 16, 23, p.robe);
        fillRect(8, 17, 15, 23, p.robeShade);
        fillRect(8, 6, 15, 13, p.skin);
        fillRect(9, 11, 14, 13, p.skinShade);
        fillRect(7, 5, 16, 7, p.hat);
        fillRect(8, 8, 15, 9, p.hatShade);
        fillRect(7, 8, 8, 11, p.hat);
        fillRect(15, 8, 16, 11, p.hat);
        fillRect(10, 2, 13, 4, p.plume);
        fillRect(11, 1, 12, 1, p.plume);
        drawEyes(9, 13, 9);
        fillRect(10, 12, 13, 12, p.eye);
        strokeRect(7, 6, 16, 23, p.outline);

        fillRect(20, 7, 20, 21, p.propShade);
        fillRect(19, 7, 21, 8, p.prop);
        fillRect(19, 6, 19, 7, p.prop);
        fillRect(21, 6, 21, 7, p.prop);
        fillRect(18, 9, 19, 10, p.plume);
      } else if (key === 'gemini') {
        // Diplomat: robe + scroll + brush.
        fillRect(7, 15, 16, 23, p.robe);
        fillRect(8, 17, 15, 23, p.robeShade);
        fillRect(8, 6, 15, 14, p.skin);
        fillRect(9, 11, 14, 14, p.skinShade);
        fillRect(7, 4, 16, 6, p.hat);
        fillRect(9, 3, 14, 3, p.hatShade);
        drawEyes(9, 13, 9);
        fillRect(10, 12, 13, 12, p.eye);
        strokeRect(7, 6, 16, 23, p.outline);

        fillRect(18, 10, 21, 15, p.scroll);
        fillRect(19, 12, 20, 13, p.scrollBand);
        fillRect(18, 9, 18, 10, p.scrollBand);
        fillRect(21, 15, 21, 16, p.scrollBand);
        fillRect(2, 11, 4, 11, p.propShade);
        fillRect(4, 10, 4, 12, p.propShade);
        fillRect(0, 10, 1, 12, p.prop);
      } else {
        // Commander: imperial crown + seal token.
        fillRect(7, 15, 16, 23, p.robe);
        fillRect(8, 17, 15, 23, p.robeShade);
        fillRect(8, 6, 15, 14, p.skin);
        fillRect(9, 11, 14, 14, p.skinShade);
        fillRect(8, 4, 15, 5, p.crown);
        px(9, 3, p.crown);
        px(11, 2, p.crown);
        px(13, 3, p.crown);
        px(15, 3, p.crown);
        fillRect(10, 6, 13, 6, p.crownShade);
        drawEyes(9, 13, 9);
        fillRect(10, 12, 13, 12, p.eye);
        fillRect(10, 18, 13, 20, p.seal);
        fillRect(11, 19, 12, 19, p.sealShade);
        strokeRect(7, 6, 16, 23, p.outline);

        fillRect(3, 7, 3, 20, p.propShade);
        fillRect(1, 7, 2, 9, p.prop);
        fillRect(1, 10, 2, 12, p.propShade);
      }

      if (variant === 1) {
        // Variant: shoulder cape layers.
        fillRect(6, 14, 8, 17, p.robeShade);
        fillRect(15, 14, 17, 17, p.robeShade);
      } else if (variant === 2) {
        // Variant: formal chest knot.
        fillRect(10, 17, 13, 18, p.prop);
        fillRect(11, 19, 12, 20, p.propShade);
      } else if (variant === 3) {
        // Variant: hairpin/crown trim.
        fillRect(9, 3, 14, 3, p.crownShade);
        px(11, 2, p.crown);
        px(12, 2, p.crown);
      } else if (variant === 4) {
        // Variant: side ornament and belt seal.
        fillRect(19, 10, 21, 12, p.seal);
        fillRect(20, 11, 20, 11, p.sealShade);
        fillRect(9, 20, 14, 20, p.scrollBand);
      }

      const rects = [`<rect width="${size}" height="${size}" fill="${p.bg}"></rect>`];
      for (let y = 0; y < size; y += 1) {
        for (let x = 0; x < size; x += 1) {
          const color = grid[y][x];
          if (!color) continue;
          rects.push(`<rect x="${x}" y="${y}" width="1" height="1" fill="${color}"></rect>`);
        }
      }
      return `<svg viewBox="0 0 ${size} ${size}" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">${rects.join('')}</svg>`;
    }

    function avatarHtml(roleId, provider, className = 'role-avatar') {
      return `<span class="${className}" aria-hidden="true">${generateAvatarSvg(roleId, provider)}</span>`;
    }

    function roleAvatarHtml(roleId, provider) {
      return avatarHtml(roleId, provider, 'role-avatar');
    }

  return {
    normalizeAvatarProvider,
    avatarHtml,
    roleAvatarHtml,
    generateAvatarSvg,
  };
}
