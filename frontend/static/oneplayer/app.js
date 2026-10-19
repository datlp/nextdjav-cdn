document.addEventListener('DOMContentLoaded', () => {
    // Tự động nhận diện Backend Server Host & Port
    const backendHost = window.__BACKEND_URL__ || window.location.origin;
    const backendPort = window.location.port || window.__BACKEND_PORT__ || 3000;

    let explorer = null;

    // Khởi tạo OnePlayer kết nối tới Backend
    const player = new VODPlayer({
        containerId: 'video-container',
        videoId: 'player',
        playBtnId: 'play-btn',
        btnPlayToggleId: 'btn-play-toggle',
        btnPrevId: 'btn-prev',
        btnNextId: 'btn-next',
        btnFullscreenId: 'btn-fullscreen',
        btnLikeId: 'btn-like',
        btnDislikeId: 'btn-dislike',
        likeCountId: 'like-count',
        musicDiscId: 'music-disc',
        hudId: 'seek-hud',
        seekbarId: 'seekbar',
        seekFillId: 'seek-fill',
        currTimeId: 'curr-time',
        durTimeId: 'dur-time',
        streamUrl: '',
        backendHost: backendHost,
        onSwipeUpperExplorer: () => {
            if (explorer) {
                explorer.openContextualDrawer();
            }
        },
        onSwipeRightToLeft: () => {
            if (explorer) {
                explorer.openContextualDrawer();
            }
        },
        onNext: () => {
            if (explorer) {
                explorer.playNextVideo();
            }
        },
        onPrev: () => {
            if (explorer) {
                explorer.playPrevVideo();
            }
        },
        onToggleQueue: (code, inQueue) => {
            if (explorer && explorer.items) {
                const found = explorer.items.find(it => (it.code || '').toUpperCase() === (code || '').toUpperCase());
                if (found) found.inDownloadQueue = inQueue;
            }
        }
    });

    window.playerInstance = player;

    const videoCarouselContainer = document.getElementById('video-carousel-container');
    const videoCarouselTrack = document.getElementById('video-carousel-track');
    const videoTitleText = document.getElementById('video-title');
    const videoReleaseDateText = document.getElementById('video-release-date');
    const videoStudioContainer = document.getElementById('video-studio-container');
    const videoActressContainer = document.getElementById('video-actress-container');
    const videoBottomTagsContainer = document.getElementById('video-bottom-tags');
    const btnFavorite = document.getElementById('btn-favorite');
    const iconFavorite = document.getElementById('icon-favorite');
    let currentVideoInfo = null;
    let carouselVideosCache = [];
    let currentCarouselThreadId = null;

    // Helper kiểm tra và cập nhật trạng thái Yêu thích (MongoDB)
    async function checkFavoriteStatus(info) {
        if (!btnFavorite || !iconFavorite || !info) return;
        const targetId = info.thread_id || info.code || info.filename || '';
        if (!targetId) return;
        try {
            const res = await fetch(`${backendHost}/api/favorites/status?id=${encodeURIComponent(targetId)}`, { credentials: 'include' });
            const data = await res.json();
            const isFav = !!(data && (data.favorited || data.is_favorited));
            btnFavorite.classList.toggle('is-favorited', isFav);
            iconFavorite.innerText = isFav ? 'favorite' : 'favorite_border';
        } catch (e) {
            console.error('[Favorites] Error checking status:', e);
        }
    }

    if (btnFavorite) {
        btnFavorite.addEventListener('click', async (e) => {
            e.stopPropagation();
            if (!currentVideoInfo) return;
            const targetId = currentVideoInfo.thread_id || currentVideoInfo.code || currentVideoInfo.filename || '';
            if (!targetId) return;

            const payload = {
                id: targetId,
                code: currentVideoInfo.code || targetId,
                thread_id: currentVideoInfo.thread_id || '',
                thread_name: currentVideoInfo.thread_name || currentVideoInfo.title || '',
                title: currentVideoInfo.title || currentVideoInfo.thread_name || targetId,
                poster_url: currentVideoInfo.poster_url || currentVideoInfo.cover_url || '',
                cover_url: currentVideoInfo.cover_url || currentVideoInfo.poster_url || '',
                release_date: currentVideoInfo.release_date || '',
                studio: currentVideoInfo.studio || currentVideoInfo.maker || '',
                box_id: currentVideoInfo.box_id || '',
                box_name: currentVideoInfo.box_name || currentVideoInfo.studio || '',
                first_media_id: currentVideoInfo.code || '',
                video_count: currentVideoInfo.video_count || 1
            };

            try {
                btnFavorite.style.pointerEvents = 'none';
                const res = await fetch(`${backendHost}/api/favorites/toggle`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    credentials: 'include',
                    body: JSON.stringify(payload)
                });
                const data = await res.json();
                const isFav = !!(data && (data.favorited || data.added));
                btnFavorite.classList.toggle('is-favorited', isFav);
                if (iconFavorite) iconFavorite.innerText = isFav ? 'favorite' : 'favorite_border';
            } catch (err) {
                console.error('[Favorites] Error toggling favorite:', err);
            } finally {
                btnFavorite.style.pointerEvents = 'auto';
            }
        });
    }


    // 1. Danh sách từ vô nghĩa phổ biến tiếng Anh & từ rác trong tiêu đề phim
    const TITLE_STOPWORDS = new Set([
        'the', 'a', 'an', 'and', 'or', 'in', 'on', 'at', 'to', 'for', 'with', 'of', 'by', 'from',
        'is', 'are', 'was', 'were', 'be', 'this', 'that', 'my', 'her', 'his', 'your', 'their', 'its',
        'our', 'all', 'some', 'any', 'no', 'not', 'only', 'own', 'other', 'into', 'over', 'after',
        'before', 'between', 'under', 'during', 'without', 'through', 'about', 'against', 'among',
        'hd', 'fhd', '4k', 'vr', 'sub', 'chinese', 'japanese', 'jav', 'uncensored', 'leaked',
        'vol', 'part', 'best', 'new', 'full', 'episode', 'special', 'edition', 'complete',
        'exclusive', 'first', 'debut', 'collection', 'series', 'missav', 'video', 'stream',
        'mp4', 'mkv', '1080p', '720p', '2160p', 'preview', 'trailer', 'cut', 'super', 'ultra',
        'leaked-uncensored', 'uncensored-leaked'
    ]);

    // 2. Pure English Hot Keywords & Descriptors Dictionary
    const HOT_KEYWORDS_DICT = [
        // Body & Physical Attributes
        { patterns: ['bouncy breasts', 'bouncy boobs', 'bouncy tits'], label: 'Bouncy Breasts', query: 'bouncy-breasts', icon: 'auto_awesome' },
        { patterns: ['sweet pheromones', 'pheromones', 'pheromone'], label: 'Pheromones', query: 'pheromones', icon: 'local_florist' },
        { patterns: ['overflowing', 'overflow'], label: 'Overflowing', query: 'overflowing', icon: 'waves' },
        { patterns: ['bouncy'], label: 'Bouncy', query: 'bouncy', icon: 'auto_awesome' },
        { patterns: ['breasts', 'boobs', 'tits', 'oppai', 'bust'], label: 'Breasts / Bust', query: 'breasts', icon: 'favorite' },
        { patterns: ['big tits', 'busty', 'huge breasts', 'massive tits', 'giant breasts'], label: 'Busty', query: 'busty', icon: 'auto_awesome' },
        { patterns: ['slender', 'slim', 'skinny', 'thin'], label: 'Slender', query: 'slender', icon: 'straighten' },
        { patterns: ['petite', 'tiny', 'small', 'short'], label: 'Petite', query: 'petite', icon: 'accessibility' },
        { patterns: ['plump', 'chubby', 'curvy', 'fleshy'], label: 'Plump / Curvy', query: 'curvy', icon: 'person' },
        { patterns: ['beautiful legs', 'legs', 'pantyhose', 'stockings', 'tights', 'thighs'], label: 'Legs / Stockings', query: 'legs', icon: 'accessibility_new' },
        { patterns: ['butt', 'ass', 'big ass', 'hip', 'hips'], label: 'Butt / Hips', query: 'butt', icon: 'favorite' },
        { patterns: ['wet', 'soaking', 'drenched', 'dripping'], label: 'Wet / Soaking', query: 'wet', icon: 'water_drop' },
        { patterns: ['tight', 'constricted', 'narrow'], label: 'Tight', query: 'tight', icon: 'compress' },
        { patterns: ['sweet', 'sweetly'], label: 'Sweet', query: 'sweet', icon: 'favorite' },

        // Actions & Verbs
        { patterns: ['wants to have sex', 'want to have sex', 'wants sex', 'want sex', 'have sex', 'having sex'], label: 'Have Sex', query: 'sex', icon: 'favorite' },
        { patterns: ['deep drilling', 'deep drill', 'drilling', 'drill'], label: 'Deep Drilling', query: 'drilling', icon: 'bolt' },
        { patterns: ['piston', 'piston action', 'pistoning'], label: 'Piston', query: 'piston', icon: 'sync' },
        { patterns: ['penetration', 'deep penetration', 'inserted', 'insertion'], label: 'Penetration', query: 'penetration', icon: 'double_arrow' },
        { patterns: ['creampie', 'nakadashi', 'internal cum', 'inside cum'], label: 'Creampie', query: 'creampie', icon: 'water_drop' },
        { patterns: ['squirt', 'squirting', 'gush', 'gushing'], label: 'Squirting', query: 'squirt', icon: 'waves' },
        { patterns: ['climax', 'orgasm', 'ecstasy', 'reach climax'], label: 'Climax / Orgasm', query: 'climax', icon: 'flash_on' },
        { patterns: ['blowjob', 'fellatio', 'oral', 'deepthroat', 'bj', 'suck', 'sucking'], label: 'Blowjob / Oral', query: 'oral', icon: 'face' },
        { patterns: ['handjob', 'hj', 'manual'], label: 'Handjob', query: 'handjob', icon: 'front_hand' },
        { patterns: ['cunnilingus', 'licking', 'lick'], label: 'Licking / Oral', query: 'licking', icon: 'sentiment_satisfied' },
        { patterns: ['cum', 'swallow', 'facial', 'bukkake'], label: 'Cum / Swallow', query: 'cum', icon: 'water_drop' },
        { patterns: ['anal', 'backdoor'], label: 'Anal', query: 'anal', icon: 'radio_button_checked' },
        { patterns: ['threesome', '3p', '3some', 'gangbang', 'orgy'], label: 'Threesome / Gangbang', query: 'group', icon: 'groups' },
        { patterns: ['massage', 'spa', 'esthe', 'oil massage'], label: 'Massage', query: 'massage', icon: 'spa' },
        { patterns: ['kiss', 'kissing', 'deep kiss'], label: 'Kissing', query: 'kiss', icon: 'favorite' },
        { patterns: ['caress', 'caressing', 'fondle', 'touching'], label: 'Caress', query: 'caress', icon: 'touch_app' },
        { patterns: ['tied up', 'bondage', 'rope', 'shibari', 'blindfold'], label: 'Bondage / Tied Up', query: 'bondage', icon: 'link' },
        { patterns: ['peeping', 'voyeur', 'spying', 'hidden camera'], label: 'Voyeur / Peeping', query: 'voyeur', icon: 'visibility' },
        { patterns: ['seduction', 'seducing', 'seduce', 'temptation', 'tempting'], label: 'Seduction', query: 'seduction', icon: 'local_fire_department' },

        // States & Feelings
        { patterns: ['horny', 'lustful', 'lewd', 'perverted', 'naughty', 'erotic'], label: 'Horny / Lewd', query: 'horny', icon: 'whatshot' },
        { patterns: ['innocent', 'pure', 'naive', 'clear'], label: 'Innocent', query: 'innocent', icon: 'auto_awesome' },
        { patterns: ['obedient', 'submissive', 'docile', 'meek'], label: 'Submissive', query: 'submissive', icon: 'pan_tool' },
        { patterns: ['dominant', 'dom', 'femdom', 'sadistic'], label: 'Dominant', query: 'dominant', icon: 'military_tech' },
        { patterns: ['shame', 'shameful', 'embarrassed', 'blushing'], label: 'Shame / Embarrassed', query: 'shame', icon: 'sentiment_very_dissatisfied' },
        { patterns: ['trembling', 'tremble', 'shiver', 'shivering'], label: 'Trembling', query: 'trembling', icon: 'vibration' },
        { patterns: ['sweat', 'sweaty', 'perspiration'], label: 'Sweaty', query: 'sweaty', icon: 'opacity' },
        { patterns: ['drunk', 'drinking', 'intoxicated', 'tipsy', 'bar'], label: 'Drunk', query: 'drunk', icon: 'local_bar' },
        { patterns: ['sleep', 'sleeping', 'asleep', 'sleeping beauty', 'somnophilia'], label: 'Sleep', query: 'sleep', icon: 'bedtime' },
        { patterns: ['hypnosis', 'hypnotic', 'mind control'], label: 'Hypnosis', query: 'hypnosis', icon: 'psychology' },
        { patterns: ['secret', 'blackmail', 'threat', 'coercion', 'extortion'], label: 'Secret / Blackmail', query: 'blackmail', icon: 'lock' },
        { patterns: ['affair', 'cheating', 'infidelity', 'adultery', 'ntr', 'netorare', 'unfaithful'], label: 'Affair / NTR', query: 'affair', icon: 'visibility_off' },
        { patterns: ['forbidden', 'taboo', 'prohibited'], label: 'Forbidden', query: 'forbidden', icon: 'block' },

        // Demographics, Roles & Scenarios
        { patterns: ['university', 'college', 'campus', 'uni', 'jd'], label: 'University / College', query: 'university', icon: 'school' },
        { patterns: ['school', 'classroom', 'student', 'uniform', 'schoolgirl', 'high school', 'jk'], label: 'School / Uniform', query: 'school', icon: 'school' },
        { patterns: ['hot spring', 'onsen', 'spa resort'], label: 'Hot Spring', query: 'onsen', icon: 'hot_tub' },
        { patterns: ['hotel', 'motel', 'love hotel', 'inn'], label: 'Hotel', query: 'hotel', icon: 'hotel' },
        { patterns: ['office', 'business', 'corporate', 'ol', 'workplace'], label: 'Office / OL', query: 'office', icon: 'apartment' },
        { patterns: ['train', 'subway', 'metro', 'chikan'], label: 'Train', query: 'train', icon: 'train' },
        { patterns: ['beach', 'resort', 'sea', 'pool', 'swimsuit', 'bikini'], label: 'Beach / Pool', query: 'beach', icon: 'beach_access' },
        { patterns: ['hospital', 'nurse', 'clinic', 'doctor', 'patient'], label: 'Nurse / Hospital', query: 'nurse', icon: 'local_hospital' },
        { patterns: ['bath', 'bathing', 'shower', 'bathroom'], label: 'Bath', query: 'bath', icon: 'bathtub' },
        { patterns: ['neighbor', 'next door', 'neighboring'], label: 'Neighbor', query: 'neighbor', icon: 'cottage' },
        { patterns: ['wife', 'housewife', 'married', 'human wife', 'bride'], label: 'Wife / Bride', query: 'wife', icon: 'favorite' },
        { patterns: ['sister', 'step-sister', 'stepsister', 'sister-in-law', 'younger sister', 'older sister'], label: 'Sister', query: 'sister', icon: 'people' },
        { patterns: ['mother', 'stepmother', 'step-mother', 'milf', 'mature', 'mother-in-law'], label: 'Milf / Mature', query: 'milf', icon: 'family_restroom' },
        { patterns: ['teacher', 'instructor', 'tutor', 'sensei', 'female teacher'], label: 'Teacher', query: 'teacher', icon: 'cast_for_education' },
        { patterns: ['secretary', 'assistant'], label: 'Secretary', query: 'secretary', icon: 'badge' },
        { patterns: ['boss', 'president', 'manager', 'executive', 'director'], label: 'Boss', query: 'boss', icon: 'work' },
        { patterns: ['co-living', 'roommate', 'living together', 'share house'], label: 'Co-living', query: 'co-living', icon: 'home' },
        { patterns: ['dating', 'first date', 'date', 'rendezvous'], label: 'Dating', query: 'dating', icon: 'favorite_border' },
        { patterns: ['maid', 'servant', 'housekeeper'], label: 'Maid', query: 'maid', icon: 'cleaning_services' },
        { patterns: ['cosplay', 'costume', 'cosplayer'], label: 'Cosplay', query: 'cosplay', icon: 'theater_comedy' },
        { patterns: ['pov', 'point of view', 'gopro', 'first person'], label: 'POV', query: 'pov', icon: 'videocam' },
        { patterns: ['virgin', 'first time', 'debut', 'deflower'], label: 'Virgin / First Time', query: 'virgin', icon: 'star' },
        { patterns: ['gal', 'gyaru', 'gyaru gal'], label: 'Gyaru / Gal', query: 'gyaru', icon: 'sparkles' },
        { patterns: ['model', 'gravure', 'idol', 'beauty'], label: 'Model / Beauty', query: 'model', icon: 'diamond' },
        { patterns: ['amateur', 'real amateur', 'non-pro'], label: 'Amateur', query: 'amateur', icon: 'verified_user' }
    ];

    function extractKeywordsFromTitle(title, studioList = [], actressList = []) {
        if (!title || typeof title !== 'string') return [];

        let cleanTitle = title
            .replace(/\[.*?\]|\(.*?\)|【.*?】|『.*?』/g, ' ')
            .replace(/[A-Za-z]{2,8}[-_ ]?\d{2,6}/gi, ' ')
            .replace(/[^a-zA-Z0-9\s-_]/g, ' ')
            .trim();

        const lowerTitle = title.toLowerCase();
        const foundKeywords = [];
        const addedQueries = new Set();

        // 1. Dynamic Regex Match: Age (e.g. "21 Years Old", "20 yo")
        const ageMatch = title.match(/\b(1[89]|[2-6]\d)\s*(?:years?\s*old|yo|y\/o)\b/i);
        if (ageMatch) {
            const ageVal = ageMatch[1];
            const ageQuery = `${ageVal}-years-old`;
            if (!addedQueries.has(ageQuery)) {
                foundKeywords.push({
                    label: `${ageVal} Years Old`,
                    query: ageQuery,
                    matchedPattern: ageMatch[0],
                    icon: 'cake'
                });
                addedQueries.add(ageQuery);
            }
        }

        // 2. Dynamic Regex Match: Cup Size (e.g. "G-Cup", "F Cup")
        const cupMatch = title.match(/\b([A-K])(?:-|\s*)?cup\b/i);
        if (cupMatch) {
            const cupVal = cupMatch[1].toUpperCase();
            const cupQuery = `${cupVal}-cup`;
            if (!addedQueries.has(cupQuery)) {
                foundKeywords.push({
                    label: `${cupVal}-Cup`,
                    query: cupQuery,
                    matchedPattern: cupMatch[0],
                    icon: 'auto_awesome'
                });
                addedQueries.add(cupQuery);
            }
        }

        // 3. Match từ điển Hot Scenarios & Attributes trước
        for (const item of HOT_KEYWORDS_DICT) {
            for (const pattern of item.patterns) {
                const reg = new RegExp(`(^|[^a-zA-Z0-9])(${pattern.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})([^a-zA-Z0-9]|$)`, 'i');
                const m = lowerTitle.match(reg);
                if (m) {
                    if (!addedQueries.has(item.query)) {
                        foundKeywords.push({
                            label: item.label,
                            query: item.query,
                            matchedPattern: pattern,
                            icon: item.icon
                        });
                        addedQueries.add(item.query);
                    }
                    break;
                }
            }
        }

        // 4. Trích xuất Dynamic Noun / Adjective / Verb Keywords từ các từ còn lại trong Title
        const excludeTokens = new Set([
            ...TITLE_STOPWORDS,
            ...actressList.map(a => a.toLowerCase()),
            ...studioList.map(s => s.toLowerCase())
        ]);

        const rawWords = cleanTitle.split(/\s+/).filter(Boolean);
        for (const word of rawWords) {
            const wLower = word.toLowerCase();
            if (wLower.length < 3 || /^\d+$/.test(wLower)) continue;
            if (excludeTokens.has(wLower)) continue;
            if (addedQueries.has(wLower)) continue;

            if (/^[a-zA-Z]{3,20}$/.test(word)) {
                const capitalized = word.charAt(0).toUpperCase() + word.slice(1).toLowerCase();
                foundKeywords.push({
                    label: capitalized,
                    query: wLower,
                    matchedPattern: word,
                    icon: 'tag'
                });
                addedQueries.add(wLower);
            }
        }

        return foundKeywords;
    }

    function renderHighlightedTitle(title, keywords = []) {
        if (!title) return 'VOD Stream';
        if (!keywords || keywords.length === 0) return escapeHtml(title);

        const matchItems = [];
        for (const kw of keywords) {
            if (kw.matchedPattern) {
                matchItems.push({ text: kw.matchedPattern, query: kw.query, label: kw.label });
            } else if (kw.query) {
                matchItems.push({ text: kw.query, query: kw.query, label: kw.label });
            }
        }

        if (matchItems.length === 0) return escapeHtml(title);

        // Sort longest pattern first so compound phrases are matched before single words
        matchItems.sort((a, b) => b.text.length - a.text.length);

        const patternStr = matchItems.map(m => m.text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|');
        const regex = new RegExp(`(${patternStr})`, 'gi');

        return title.split(regex).map(chunk => {
            if (!chunk) return '';
            const lower = chunk.toLowerCase();
            const found = matchItems.find(m => m.text.toLowerCase() === lower);
            if (found) {
                const isSearchActive = explorer && explorer.searchQuery && explorer.searchQuery.toLowerCase() === found.query.toLowerCase();
                return `<span class="title-keyword-highlight ${isSearchActive ? 'active' : ''}" data-query="${escapeHtml(found.query)}" title="Click to filter: ${escapeHtml(found.label)}">${escapeHtml(chunk)}</span>`;
            }
            return escapeHtml(chunk);
        }).join('');
    }

    function escapeHtml(str) {
        if (!str) return '';
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    function toggleKeywordFilter(q) {
        if (!explorer || !q) return;
        if (explorer.searchQuery && explorer.searchQuery.toLowerCase() === q.toLowerCase()) {
            // Toggle off
            explorer.searchQuery = '';
            if (explorer.inputSearchVideo) explorer.inputSearchVideo.value = '';
            if (explorer.btnClearSearch) explorer.btnClearSearch.classList.add('hidden');
            if (explorer.searchWrapper) explorer.searchWrapper.classList.remove('active');
            explorer.refreshCurrentWatchContext();
        } else {
            // Toggle on
            explorer.searchQuery = q;
            if (explorer.inputSearchVideo) explorer.inputSearchVideo.value = q;
            if (explorer.btnClearSearch) explorer.btnClearSearch.classList.remove('hidden');
            if (explorer.searchWrapper) explorer.searchWrapper.classList.add('active');
            explorer.lastToggledFilter = { type: 'search', value: q };
            explorer.refreshCurrentWatchContext();
        }
        if (window.updateBottomTagsActiveState) {
            window.updateBottomTagsActiveState();
        }
    }

    function cleanEntityList(val) {
        if (!val) return [];
        if (Array.isArray(val)) {
            const res = [];
            val.forEach(item => {
                res.push(...cleanEntityList(item));
            });
            return res;
        }
        let str = String(val).trim();
        if (!str) return [];
        if (str.startsWith('[') && str.endsWith(']')) {
            try {
                const parsed = JSON.parse(str);
                if (Array.isArray(parsed)) {
                    return cleanEntityList(parsed);
                }
            } catch (e) {
                str = str.slice(1, -1);
            }
        }
        return str.replace(/\//g, ',').split(',')
            .map(s => s.trim().replace(/^['"\[\]]+|['"\[\]]+$/g, '').trim())
            .filter(Boolean);
    }

    function updatePlayerBottomInfo(info) {
        if (!info) return;
        currentVideoInfo = info;
        const code = (info.code || info.filename || '').toUpperCase();
        const displayTitle = info.title || info.filename || code || 'VOD Stream';

        // Parse Studio & Actress data first for keyword exclusion
        const studioList = cleanEntityList(info.maker || info.studio || info.studio_display || '');
        const actList = cleanEntityList(info.actress || info.actresses || info.actresses_display || '');

        // Extract Smart Keywords
        const keywords = extractKeywordsFromTitle(info.title || info.filename || '', studioList, actList);

        // Highlight Keywords directly inside #video-title
        if (videoTitleText) {
            videoTitleText.innerHTML = renderHighlightedTitle(displayTitle, keywords);
            videoTitleText.style.color = '#eeeeee';

            // Bind click on in-title keyword highlights
            videoTitleText.querySelectorAll('.title-keyword-highlight').forEach(el => {
                el.onclick = (e) => {
                    e.stopPropagation();
                    const q = el.getAttribute('data-query');
                    toggleKeywordFilter(q);
                };
            });
        }

        if (videoReleaseDateText) {
            const relDate = info.release_date || info.releaseDate || '';
            if (relDate && window.formatRelativeReleaseDate) {
                const formatted = window.formatRelativeReleaseDate(relDate);
                videoReleaseDateText.innerText = `📅 ${formatted}`;
                videoReleaseDateText.style.display = 'block';
            } else {
                videoReleaseDateText.innerText = '';
                videoReleaseDateText.style.display = 'none';
            }
        }

        // Global helper to refresh bottom tag active state
        window.updateBottomTagsActiveState = () => {
            if (!explorer) return;
            document.querySelectorAll('.oneplayer-bottom-info .bottom-tag').forEach(tag => {
                const type = tag.getAttribute('data-type');
                const val = tag.getAttribute('data-val');
                const isActive = explorer.isFilterActive(type, val);
                tag.classList.toggle('active', isActive);
            });
            document.querySelectorAll('.video-caption .title-keyword-highlight').forEach(hl => {
                const q = hl.getAttribute('data-query');
                const isActive = explorer.searchQuery && explorer.searchQuery.toLowerCase() === (q || '').toLowerCase();
                hl.classList.toggle('active', !!isActive);
            });
        };

        // Studio Tag (Đưa lên bên phải #video-release-date)
        if (videoStudioContainer) {
            if (studioList.length > 0) {
                videoStudioContainer.innerHTML = studioList.map(st => {
                    const stEsc = escapeHtml(st);
                    const isActive = explorer && explorer.isFilterActive('studios', st);
                    return `
                        <span class="bottom-tag studio-tag ${isActive ? 'active' : ''}" data-type="studios" data-val="${stEsc}" title="Bật/Tắt lọc theo Studio ${stEsc}">
                            <span class="material-symbols-outlined bottom-tag-icon">movie</span>
                            <span>${stEsc}</span>
                        </span>
                    `;
                }).join('');
                videoStudioContainer.style.display = 'inline-flex';
                videoStudioContainer.querySelectorAll('.bottom-tag').forEach(tag => {
                    tag.onclick = (e) => {
                        e.stopPropagation();
                        const val = tag.getAttribute('data-val');
                        if (explorer) {
                            explorer.toggleFilter('studios', val);
                        }
                    };
                });
            } else {
                videoStudioContainer.innerHTML = '';
                videoStudioContainer.style.display = 'none';
            }
        }

        // Actress Tag (Gộp vào .oneplayer-bottom-meta-row cạnh Studio)
        if (videoActressContainer) {
            if (actList.length > 0) {
                videoActressContainer.innerHTML = actList.map(act => {
                    const actEsc = escapeHtml(act);
                    const isActive = explorer && explorer.isFilterActive('actresses', act);
                    return `
                        <span class="bottom-tag actress-tag ${isActive ? 'active' : ''}" data-type="actresses" data-val="${actEsc}" title="Bật/Tắt lọc theo Diễn viên ${actEsc}">
                            <span class="material-symbols-outlined bottom-tag-icon">person</span>
                            <span>${actEsc}</span>
                        </span>
                    `;
                }).join('');
                videoActressContainer.style.display = 'inline-flex';
                videoActressContainer.querySelectorAll('.bottom-tag').forEach(tag => {
                    tag.onclick = (e) => {
                        e.stopPropagation();
                        const val = tag.getAttribute('data-val');
                        if (explorer) {
                            explorer.toggleFilter('actresses', val);
                        }
                    };
                });
            } else {
                videoActressContainer.innerHTML = '';
                videoActressContainer.style.display = 'none';
            }
        }

        // Thể loại (Genres Tags nằm riêng hàng dưới trong #video-bottom-tags)
        if (videoBottomTagsContainer) {
            const genreList = cleanEntityList(info.genres || info.genresStr || info.genres_display || info.genre || '');
            if (genreList.length > 0) {
                const genHtml = genreList.map(g => {
                    const gEsc = escapeHtml(g);
                    const isActive = explorer && explorer.isFilterActive('genres', g);
                    return `
                        <span class="bottom-tag genre-tag ${isActive ? 'active' : ''}" data-type="genres" data-val="${gEsc}" title="Bật/Tắt lọc theo Thể loại ${gEsc}">
                            <span class="material-symbols-outlined bottom-tag-icon">category</span>
                            <span>${gEsc}</span>
                        </span>
                    `;
                }).join('');
                videoBottomTagsContainer.innerHTML = `<div class="bottom-tags-group genre-group">${genHtml}</div>`;
                videoBottomTagsContainer.style.display = 'flex';
                videoBottomTagsContainer.querySelectorAll('.bottom-tag').forEach(tag => {
                    tag.onclick = (e) => {
                        e.stopPropagation();
                        const val = tag.getAttribute('data-val');
                        if (explorer) {
                            explorer.toggleFilter('genres', val);
                        }
                    };
                });
            } else {
                videoBottomTagsContainer.innerHTML = '';
                videoBottomTagsContainer.style.display = 'none';
            }
        }

        // Render Persistent Video Carousel Bar
        renderVideoCarousel(info);

        // Check & Update Favorite Heart Icon (MongoDB)
        checkFavoriteStatus(info);
    }

    /**
     * Render Persistent Video Carousel (Hiển thị dải video cuộn ngang trên #video-title)
     * - Focus vào video đang phát (is-current-playing).
     * - Tối đa 20 video; nếu > 20 video thì hiện nút "Xem tất cả" mở drawer.
     * - Bấm vào video nào phát ngay lập tức tới video đó.
     */
    async function renderVideoCarousel(info) {
        if (!videoCarouselContainer || !videoCarouselTrack) return;
        if (!info) {
            videoCarouselContainer.classList.add('hidden');
            return;
        }

        const currentCode = (info.code || info.filename || '').trim().toUpperCase();
        let threadId = info.thread_id ? String(info.thread_id) : (explorer && explorer.currentContext && explorer.currentContext.threadId ? String(explorer.currentContext.threadId) : '');
        let boxId = info.box_id ? String(info.box_id) : (explorer && explorer.currentContext && explorer.currentContext.boxId ? String(explorer.currentContext.boxId) : '');

        let videos = [];

        // 1. Nếu có threadId, tải danh sách video của thread
        if (threadId) {
            if (currentCarouselThreadId === threadId && Array.isArray(carouselVideosCache) && carouselVideosCache.length > 0) {
                videos = carouselVideosCache;
            } else if (explorer && explorer.currentContext && String(explorer.currentContext.threadId) === String(threadId) && Array.isArray(explorer.videos) && explorer.videos.length > 0) {
                videos = [...explorer.videos];
                currentCarouselThreadId = threadId;
                carouselVideosCache = videos;
            } else {
                try {
                    const res = await fetch(`${backendHost}/api/videos?thread_id=${encodeURIComponent(threadId)}&limit=60`, { credentials: 'include' });
                    const data = await res.json();
                    videos = (data && (data.videos || data.entries)) || [];
                    if (videos.length > 0) {
                        currentCarouselThreadId = threadId;
                        carouselVideosCache = videos;
                    }
                } catch (e) {
                    console.debug('Error fetching thread videos for carousel:', e);
                }
            }
        }

        // 2. Fallback: Nếu thread chỉ có 1 video hoặc không có threadId, dùng danh sách videos hiện có của Explorer hoặc tải video cùng Box
        if ((!videos || videos.length <= 1) && Array.isArray(explorer?.videos) && explorer.videos.length > 1) {
            videos = [...explorer.videos];
        } else if ((!videos || videos.length <= 1) && boxId) {
            try {
                const res = await fetch(`${backendHost}/api/videos?box_id=${encodeURIComponent(boxId)}&limit=40`, { credentials: 'include' });
                const data = await res.json();
                const boxVids = (data && (data.videos || data.entries)) || [];
                if (boxVids.length > 1) {
                    videos = boxVids;
                }
            } catch (e) {
                console.debug('Error fetching box videos for carousel:', e);
            }
        } else if (!videos || videos.length <= 1) {
            // Fallback cuối cùng: nạp danh sách video gần đây
            try {
                const res = await fetch(`${backendHost}/api/videos?limit=30`, { credentials: 'include' });
                const data = await res.json();
                const feedVids = (data && (data.videos || data.entries)) || [];
                if (feedVids.length > 1) {
                    videos = feedVids;
                }
            } catch (e) {}
        }

        // Nếu vẫn chỉ có 1 hoặc 0 video thì ẩn carousel
        if (!videos || videos.length <= 1) {
            videoCarouselContainer.classList.add('hidden');
            return;
        }

        videoCarouselContainer.classList.remove('hidden');
        videoCarouselTrack.innerHTML = '';

        const maxDisplay = 20;
        const totalVideosCount = videos.length;
        const displayVideos = videos.slice(0, maxDisplay);

        const frag = document.createDocumentFragment();
        let activeEl = null;

        displayVideos.forEach(v => {
            const vCode = (v.code || v.filename || v.name || '').trim().toUpperCase();
            const isPlaying = vCode && vCode === currentCode;
            const coverSrc = v.cover_url || v.coverUrl || (vCode ? `${backendHost}/api/poster/${encodeURIComponent(vCode)}` : '');
            const titleEsc = escapeHtml(v.title || vCode);

            const card = document.createElement('div');
            card.className = `video-carousel-item ${isPlaying ? 'is-current-playing' : ''}`;
            card.setAttribute('data-code', vCode);
            card.title = `${titleEsc} (Bấm để phát ngay)`;

            card.innerHTML = `
                <div class="video-carousel-thumb-wrap">
                    ${coverSrc ? `<img src="${coverSrc}" class="video-carousel-thumb" alt="${titleEsc}" loading="lazy" />` : `<div class="video-carousel-thumb-placeholder"><span class="material-symbols-outlined">movie</span></div>`}
                    <div class="video-carousel-play-overlay">
                        <span class="material-symbols-outlined">${isPlaying ? 'equalizer' : 'play_circle'}</span>
                    </div>
                </div>
                <div class="video-carousel-info">
                    <span class="video-carousel-code">${vCode || titleEsc}</span>
                </div>
            `;

            if (isPlaying) {
                activeEl = card;
            }

            card.addEventListener('click', (e) => {
                e.stopPropagation();
                if (isPlaying) return;
                if (explorer) {
                    explorer.selectVideo(v);
                }
            });

            frag.appendChild(card);
        });

        // Nếu tổng số video > 20, hiển thị nút Show All ở cuối dải để mở drawer Explorer
        if (totalVideosCount > maxDisplay) {
            const btnShowAll = document.createElement('div');
            btnShowAll.className = 'video-carousel-show-all-btn';
            btnShowAll.title = `Xem toàn bộ ${totalVideosCount} video trong danh sách (Mở Drawer)`;
            btnShowAll.innerHTML = `
                <span class="material-symbols-outlined">view_cozy</span>
                <span class="video-carousel-show-all-title">Xem tất cả</span>
                <span class="video-carousel-show-all-count">${totalVideosCount} video</span>
            `;
            btnShowAll.addEventListener('click', (e) => {
                e.stopPropagation();
                if (explorer) {
                    if (threadId) {
                        explorer.openThreadVideos(threadId, { autoScroll: true });
                    }
                    explorer.open();
                }
            });
            frag.appendChild(btnShowAll);
        }

        videoCarouselTrack.appendChild(frag);

        // Tự động cuộn mượt đến video đang phát (Center Focus)
        if (activeEl) {
            requestAnimationFrame(() => {
                try {
                    activeEl.scrollIntoView({ behavior: 'smooth', inline: 'center', block: 'nearest' });
                } catch (e) {}
            });
        }
    }

    function buildStreamSources(info) {
        if (!info) return { sources: [], preferredId: 'cdn' };
        const code = (info.code || info.filename || '').toUpperCase();
        const cleanCode = code.replace(/\.(TS|MP4|MKV|DATTS|M3U8)$/i, '');
        let gdriveUrl = info.stream_url || info.video_url || '';
        if (!gdriveUrl || !gdriveUrl.startsWith('/api/gdrive/hls/')) {
            gdriveUrl = `/api/gdrive/hls/${encodeURIComponent(info.code || cleanCode)}.m3u8`;
        }
        const streamUrl = info.stream_url || info.video_url || info.url || '';

        // Phân biệt chế độ GDrive-only vs CDN
        const isGDriveMode = !!(window.__IS_GDRIVE_ONLY__ || ['3001', '3005', '3011', '5011'].includes(window.location.port));

        const sources = [];
        if (isGDriveMode) {
            sources.push({
                id: 'gdrive',
                type: 'gdrive',
                label: 'Google Drive HLS',
                icon: 'cloud_done',
                tag: 'GDRIVE',
                url: gdriveUrl,
                desc: 'Google Drive Keyframes Byte-Range Stream',
                priority: 1000
            });
            return { sources, preferredId: 'gdrive' };
        }

        // Port 3000: Chỉ phát duy nhất luồng Cloudflare CDN gốc
        const cdnUrl = (streamUrl && !streamUrl.includes('/api/gdrive/stream/')) ? streamUrl : (info.url || info.original_url || '');
        sources.push({
            id: 'cdn',
            type: 'standard',
            label: 'Cloudflare CDN',
            tag: 'CDN',
            icon: 'bolt',
            url: cdnUrl || streamUrl,
            desc: 'CDN Stream Direct',
            priority: 1000
        });

        return { sources, preferredId: 'cdn' };
    }

    // Khởi tạo Explorer Modal
    explorer = new VODExplorer({
        backendHost: backendHost,
        onVideoSelect: (info) => {
            if (info && (info.code || info.filename || info.stream_url || info.video_url)) {
                const code = (info.code || info.filename || '').toUpperCase();
                if (info.filename || info.code) explorer.setCurrentPlayingFilename(info.filename || info.code);

                if (info.thread_id || info.box_id) {
                    explorer.setCurrentContext({
                        boxId: info.box_id || '',
                        boxName: info.box_name || (Array.isArray(info.genres) ? info.genres[0] : '') || '',
                        threadId: info.thread_id || '',
                        threadName: info.thread_name || info.title || '',
                        videoCount: info.video_count || info.total_media || 0,
                        mediaId: code
                    });
                }

                updatePlayerBottomInfo(info);
                
                const hasGDrive = !!(info.is_gdrive || info.hasGDrive || info.gdrive_video_id);
                player.setVideoPathAndFavorite(info.path || info.code, info.favorited, info.filename || info.code, info.title, false, hasGDrive);
                
                const coverSrc = info.cover_url || info.coverUrl || `/api/poster/${encodeURIComponent(code)}`;
                const { sources, preferredId } = buildStreamSources(info);
                player.setSources(sources, preferredId);

                const activeSrc = sources.find(s => s.id === preferredId) || sources[0];
                const playUrl = activeSrc ? activeSrc.url : (info.stream_url || info.video_url || '');

                if (code) {
                    if (window.location.pathname !== `/${code}`) {
                        window.history.replaceState(null, '', `/${code}`);
                    }
                    if (window.location.hash) {
                        window.history.replaceState(null, '', window.location.pathname);
                    }
                }

                if (playUrl) {
                    const effectiveStartTime = (info.start_offset_sec && info.start_offset_sec > 0) ? info.start_offset_sec : 0;
                    player.loadStream(playUrl, {
                        code: info.code || code,
                        title: info.title || info.code,
                        coverSrc: coverSrc,
                        startTime: effectiveStartTime,
                        status: `Đang phát từ ${activeSrc ? activeSrc.label : 'NextDJAV'}...`
                    });
                } else {
                    player.showLoading({
                        title: info.title || info.code,
                        coverSrc: coverSrc,
                        status: '⚠️ Video này chưa có link phát!'
                    });
                }
            }
        }
    });

    window.explorerInstance = explorer;

    // Quản lý Khởi động (Startup):
    // 1. Nếu có video trên URL (/play/B414847B... hoặc /B414847B... hoặc window.__INITIAL_MEDIA_ID__): Tải và phát video đó ngay lập tức!
    // 2. Nếu không có mã video trên URL: Kiểm tra lịch sử xem gần nhất để khôi phục tiến trình.
    // 3. Nếu chưa từng xem: Mở danh sách hộp (Explorer) để chọn video đầu tiên.
    let pathParam = (window.location.pathname || '').replace(/^\/+/, '').trim();
    if (pathParam.startsWith('play/')) {
        pathParam = pathParam.substring(5).trim();
    } else if (pathParam === 'play' || pathParam === 'oneplayer' || pathParam === 'sources' || pathParam.startsWith('api/') || pathParam.startsWith('static/')) {
        pathParam = '';
    }
    const initialTargetId = (window.__INITIAL_MEDIA_ID__ || pathParam || '').trim();

    const token = localStorage.getItem('auth_token') || '';
    const headers = token ? { 'Authorization': `Bearer ${token}` } : {};

    async function playInitialVideoById(targetCode, progressSec = 0) {
        player.showLoading({
            title: 'NextDJAV Cinema',
            status: 'Đang nạp video...'
        });
        try {
            const vidRes = await fetch(`${backendHost}/api/video/${encodeURIComponent(targetCode)}`, { credentials: 'include', headers });
            const vidData = await vidRes.json();
            const video = (vidData && vidData.video) ? vidData.video : { code: targetCode, filename: targetCode };

            if (explorer) {
                explorer.setCurrentContext({
                    boxId: video.box_id || '',
                    boxName: video.box_name || '',
                    threadId: video.thread_id || '',
                    threadName: video.thread_name || '',
                    videoCount: video.video_count || video.total_media || 0,
                    mediaId: targetCode
                });
                explorer.setCurrentPlayingFilename(targetCode);
                // Nạp danh sách quanh targetCode vào explorer để xác định đúng page và adjacent video ngay lập tức
                explorer.fetchDirectory('all', { aroundFilename: targetCode, autoScroll: true });
                explorer.preloadAdjacentFeedData(targetCode);
            }

            updatePlayerBottomInfo(video);
            const hasGDrive = !!(video.is_gdrive || video.hasGDrive || video.gdrive_video_id);
            player.setVideoPathAndFavorite(targetCode, false, targetCode, video.title, false, hasGDrive);
            const coverSrc = video.cover_url || video.coverUrl || `/api/poster/${encodeURIComponent(targetCode)}`;

            const { sources, preferredId } = buildStreamSources(video);
            player.setSources(sources, preferredId);
            const activeSrc = sources.find(s => s.id === preferredId) || sources[0];
            const playUrl = activeSrc ? activeSrc.url : (video.stream_url || video.video_url || '');

            if (window.location.pathname !== `/play/${targetCode}` && window.location.pathname !== `/${targetCode}`) {
                window.history.replaceState(null, '', `/play/${targetCode}`);
            }
            if (window.location.hash) {
                window.history.replaceState(null, '', window.location.pathname);
            }

            if (playUrl) {
                // Ưu tiên: 1. Khôi phục tiến trình người dùng đã xem (> 0s)
                //          2. Hoặc tự động skip qua màn hình đen mở đầu (start_offset_sec)
                const effectiveStartTime = (progressSec && progressSec > 0) 
                    ? progressSec 
                    : ((video.start_offset_sec && video.start_offset_sec > 0) ? video.start_offset_sec : 0);

                player.loadStream(playUrl, {
                    code: video.code || targetCode,
                    title: video.title || targetCode,
                    coverSrc: coverSrc,
                    startTime: effectiveStartTime,
                    status: (progressSec > 0) ? 'Đang khôi phục tiến trình xem...' : `Đang phát từ ${activeSrc ? activeSrc.label : 'NextDJAV'}...`
                });
            } else {
                player.hideLoading();
            }
        } catch (err) {
            console.error('[OnePlayer] Error playing initial target video:', err);
            player.hideLoading();
            if (explorer) {
                explorer.openContextualDrawer();
            }
        }
    }

    if (initialTargetId) {
        // Trực tiếp mở video theo URL sạch dạng /:media_id
        playInitialVideoById(initialTargetId, 0);
    } else {
        // Truy cập "/" -> Mở thẳng danh sách toàn bộ Video trên Google Drive
        player.hideLoading();
        if (explorer) {
            explorer.openContextualDrawer();
        }
    }
});

