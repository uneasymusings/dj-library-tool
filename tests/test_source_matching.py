"""Search-result ranking, comment hints and set tracklists, from real observed fixtures."""

import pytest

from djlib.application.source_matching import comment_hints, rank_sources, tracklist_from_source
from djlib.application.tracklists import parse_tracklist

GEM_LINGO = {"artist": "Overmono", "title": "Gem Lingo", "version": ""}
LASSO = {"artist": "Phoenix", "title": "Lasso", "version": ""}


def entry(provider, title, uploader, duration, views=None):
    return {
        "provider": provider,
        "url": f"https://example.test/{provider}/{uploader}/{title}",
        "title": title,
        "uploader": uploader,
        "duration": duration,
        "view_count": views,
    }


def yt(title, uploader="Uploads", duration=200, views=None):
    return entry("youtube", title, uploader, duration, views)


def sc(title, uploader="Uploads", duration=200, views=None):
    return entry("soundcloud", title, uploader, duration, views)


def by_title(ranked):
    return {item["title"]: item for item in ranked}


GEM_LINGO_RESULTS = [
    yt("Overmono - Gem Lingo (ovr now) (ft. Ruthven) [Audio]", "Overmono", 231, 312_000),
    yt("Overmono - Gem Lingo (ovr now) (Osheaga 2024, Montreal)", "Festival Clips", 231, 2_100),
    sc("Gem Lingo (ovr now)", "Overmono", 30.0),
    sc("Overmono, Ruthven - Gem Lingo (Ozzi Remix)", "Ozzi", 346),
    sc("Gem Lingo (ovr now) [shingees bootleg]", "shingees", 252),
    sc("Overmono - Gem Lingo (ovr now) [shingees bootleg]", "shingees", 252),
]
LASSO_RESULTS = [
    sc("Lasso", "Phoenix", 167.9),
    sc("Lasso", "Glassnote Records", 167.9),
    yt("Phoenix - Lasso", "Kad Laser", 170, 4_400_000),
    yt("Phoenix: NPR Music Tiny Desk Concert", "NPR Music", 756, 3_000_000),
    yt("Phoenix - Lasso [ Live on Letterman, NY, USA - 2013 ]", "Late Night Archive", 175, 80_000),
    sc("Phoenix - Lasso [PV Nova Remix]", "PV Nova", 214),
    sc("Phoenix - Lasso (Two Door Cinema Club Remix)", "Two Door Cinema Club", 289),
]


def test_official_audio_upload_wins_gem_lingo_confidently():
    ranked = rank_sources(GEM_LINGO, GEM_LINGO_RESULTS)
    best = ranked[0]
    assert best["title"] == "Overmono - Gem Lingo (ovr now) (ft. Ruthven) [Audio]"
    assert best["confident"] is True
    assert best["score"] >= 0.75
    assert {"official artist upload", "official audio", "high view count"} <= set(best["reasons"])
    assert not any(item["confident"] for item in ranked[1:])
    titles = by_title(ranked)
    assert "Gem Lingo (ovr now)" not in titles  # 30-second Go+ preview
    assert "Gem Lingo (ovr now) [shingees bootleg]" not in titles  # artist nowhere
    live = titles["Overmono - Gem Lingo (ovr now) (Osheaga 2024, Montreal)"]
    assert "festival or venue upload" in live["reasons"] and live["score"] < 0.5
    remix = titles["Overmono, Ruthven - Gem Lingo (Ozzi Remix)"]
    assert "remix not requested" in remix["reasons"] and remix["score"] < 0.5
    bootleg = titles["Overmono - Gem Lingo (ovr now) [shingees bootleg]"]
    assert "bootleg not requested" in bootleg["reasons"] and bootleg["score"] < 0.5


def test_lasso_ranks_artist_upload_then_plausible_reuploads():
    ranked = rank_sources(LASSO, LASSO_RESULTS)
    assert [(item["title"], item["uploader"]) for item in ranked[:3]] == [
        ("Lasso", "Phoenix"),
        ("Phoenix - Lasso", "Kad Laser"),
        ("Lasso", "Glassnote Records"),
    ]
    assert ranked[0]["confident"] is True
    assert "official artist upload" in ranked[0]["reasons"]
    assert all(item["score"] >= 0.6 for item in ranked[:3])
    assert "duration matches others" in ranked[1]["reasons"]
    assert {"label upload", "artist not in title"} <= set(ranked[2]["reasons"])
    titles = by_title(ranked)
    assert "Phoenix: NPR Music Tiny Desk Concert" not in titles
    assert (
        "live recording"
        in titles["Phoenix - Lasso [ Live on Letterman, NY, USA - 2013 ]"]["reasons"]
    )
    assert "remix not requested" in titles["Phoenix - Lasso [PV Nova Remix]"]["reasons"]
    assert all(0 <= item["score"] <= 1 for item in ranked)
    assert all(item["score"] < 0.5 for item in ranked[3:])


@pytest.mark.parametrize(
    "requested",
    [
        {"artist": "Phoenix", "title": "Lasso", "version": "Two Door Cinema Club Remix"},
        {"artist": "Phoenix", "title": "Lasso (Two Door Cinema Club Remix)", "version": ""},
    ],
)
def test_requested_remix_keeps_only_that_remix(requested):
    ranked = rank_sources(requested, [*LASSO_RESULTS, sc("Lasso (Remix)", "Phoenix", 280)])
    assert [item["title"] for item in ranked] == ["Phoenix - Lasso (Two Door Cinema Club Remix)"]
    assert ranked[0]["confident"] is True
    assert "remix not requested" not in ranked[0]["reasons"]


def test_required_words_and_uploader_exemptions():
    ranked = rank_sources(
        LASSO,
        [
            yt("Phoenix - Entertainment", "Phoenix", 200),
            sc("Lasso", "Some DJ", 168),
            yt("Lasso", "Phoenix - Topic", 168),
            yt("Lasso (Official Video)", "PhoenixVEVO", 180),
            yt("Röyksopp & Phoenix - Lasso", "Fan", 168),
        ],
    )
    titles = [(item["title"], item["uploader"]) for item in ranked]
    assert ("Phoenix - Entertainment", "Phoenix") not in titles
    assert ("Lasso", "Some DJ") not in titles
    topic = ranked[0]
    assert (topic["title"], topic["uploader"]) == ("Lasso", "Phoenix - Topic")
    assert {"official artist upload", "YouTube Topic channel"} <= set(topic["reasons"])
    vevo = next(item for item in ranked if item["uploader"] == "PhoenixVEVO")
    assert "official artist upload" in vevo["reasons"]


def test_accents_and_case_do_not_block_a_match():
    requested = {"artist": "Röyksopp", "title": "Here She Comes Again", "version": ""}
    ranked = rank_sources(requested, [yt("ROYKSOPP - here she comes again", "Fan", 220)])
    assert len(ranked) == 1


@pytest.mark.parametrize(
    ("title", "reason"),
    [
        ("Phoenix - Lasso RMX", "remix not requested"),
        ("Phoenix - Lasso (Radio Edit)", "edit not requested"),
        ("Phoenix - Lasso (VIP)", "vip not requested"),
        ("Phoenix - Lasso (Rework)", "rework not requested"),
        ("Phoenix - Lasso (Flip)", "flip not requested"),
        ("Phoenix - Lasso (Dub)", "dub not requested"),
        ("Phoenix - Lasso (Live)", "live recording"),
        ("Phoenix - Lasso (Cover)", "cover not requested"),
        ("Phoenix - Lasso (Karaoke Version)", "karaoke not requested"),
        ("Phoenix - Lasso (Instrumental)", "instrumental not requested"),
        ("Phoenix - Lasso (Acapella)", "acapella not requested"),
        ("Phoenix - Lasso (Sped Up)", "sped up not requested"),
        ("Phoenix - Lasso (Slowed + Reverb)", "slowed not requested"),
        ("Phoenix - Lasso (Slowed + Reverb)", "reverb not requested"),
        ("Phoenix - Lasso (Nightcore)", "nightcore not requested"),
        ("Phoenix - Lasso (8D Audio)", "8d not requested"),
        ("Phoenix - Lasso Mashup", "mashup"),
        ("Phoenix x Daft Punk - Lasso", "mashup"),
        ("Phoenix - Lasso x One More Time", "mashup"),
        ("Phoenix - Lasso / Entertainment / 1901", "medley"),
        ("Phoenix - Lasso (Coachella 2013)", "festival or venue upload"),
        ("Phoenix - Lasso (Club Mix)", "different mix"),
    ],
)
def test_unrequested_versions_are_penalised(title, reason):
    plain, other = rank_sources(LASSO, [yt("Phoenix - Lasso", "Fan", 168), yt(title, "Fan", 168)])
    assert plain["title"] == "Phoenix - Lasso"
    assert reason in other["reasons"]
    assert other["score"] <= plain["score"] - 0.4 + 1e-9
    assert other["confident"] is False


def test_lyrics_and_extended_are_small_penalties():
    ranked = rank_sources(
        GEM_LINGO,
        [
            yt("Overmono - Gem Lingo", "Fan"),
            yt("Overmono - Gem Lingo (Lyrics)", "Fan"),
            yt("Overmono - Gem Lingo (Extended Mix)", "Fan", 320),
        ],
    )
    titles = by_title(ranked)
    plain = titles["Overmono - Gem Lingo"]["score"]
    lyrics = titles["Overmono - Gem Lingo (Lyrics)"]
    extended = titles["Overmono - Gem Lingo (Extended Mix)"]
    assert "lyrics upload" in lyrics["reasons"]
    assert lyrics["score"] == pytest.approx(plain - 0.03)
    assert "extended mix not requested" in extended["reasons"]
    assert plain - 0.4 < extended["score"] < plain


def test_requested_version_must_be_present_and_original_mix_means_no_version():
    extended = {"artist": "Overmono", "title": "Gem Lingo", "version": "Extended Mix"}
    candidates = [
        yt("Overmono - Gem Lingo", "Fan"),
        yt("Overmono - Gem Lingo (Extended Mix)", "Fan", 320),
        yt("Overmono - Gem Lingo (Original Mix)", "Fan"),
    ]
    ranked = rank_sources(extended, candidates)
    assert [item["title"] for item in ranked] == ["Overmono - Gem Lingo (Extended Mix)"]
    assert ranked[0]["reasons"] == []

    original = {"artist": "Overmono", "title": "Gem Lingo", "version": "Original Mix"}
    ranked = by_title(rank_sources(original, [*candidates, yt("Overmono - Gem Lingo (Remix)")]))
    assert ranked["Overmono - Gem Lingo (Original Mix)"]["score"] == pytest.approx(
        ranked["Overmono - Gem Lingo"]["score"]
    )
    assert "remix not requested" in ranked["Overmono - Gem Lingo (Remix)"]["reasons"]


@pytest.mark.parametrize(
    ("title", "duration"),
    [
        ("Overmono - Gem Lingo", 30.0),
        ("Overmono - Gem Lingo", 59.9),
        ("Overmono - Gem Lingo", 15 * 60 + 1),
        ("Overmono - Gem Lingo (Boiler Room set)", 240),
        ("Overmono live set ft. Gem Lingo", 240),
        ("Overmono - Essential Mix (Gem Lingo)", 240),
        ("Overmono - Gem Lingo | Tiny Desk", 240),
        ("Overmono FULL SET - Gem Lingo", 240),
    ],
)
def test_previews_long_uploads_and_sets_are_rejected(title, duration):
    assert rank_sources(GEM_LINGO, [yt(title, "Overmono", duration)]) == []


def test_requested_live_version_accepts_venue_names():
    requested = {"artist": "Daft Punk", "title": "Around the World", "version": "Live"}
    ranked = rank_sources(
        requested,
        [
            yt("Daft Punk - Around the World (Live at Alive 2007)"),
            yt("Daft Punk - Around the World"),
        ],
    )
    assert [(item["title"], item["reasons"]) for item in ranked] == [
        ("Daft Punk - Around the World (Live at Alive 2007)", [])
    ]


def test_sets_are_kept_when_a_mix_is_requested():
    requested = {"artist": "Overmono", "title": "Essential Mix", "version": ""}
    ranked = rank_sources(requested, [sc("Overmono - Essential Mix 2023", "BBC", 7200)])
    assert [item["duration"] for item in ranked] == [7200]


def test_duration_bonus_needs_agreement_with_the_median_of_others():
    ranked = by_title(
        rank_sources(
            GEM_LINGO,
            [
                yt("Overmono - Gem Lingo", "A", 231),
                yt("Overmono - Gem Lingo (HQ)", "B", 233),
                yt("Overmono - Gem Lingo (Visualiser)", "C", 239),
                yt("Overmono - Gem Lingo (Official Video)", "D", 290),
                yt("Overmono - Gem Lingo [HD]", "E", None),
                yt("Overmono - Gem Lingo (Remix)", "F", 233),
            ],
        )
    )
    matching = {
        title for title, item in ranked.items() if "duration matches others" in item["reasons"]
    }
    assert matching == {
        "Overmono - Gem Lingo",
        "Overmono - Gem Lingo (HQ)",
        "Overmono - Gem Lingo (Visualiser)",
    }
    assert ranked["Overmono - Gem Lingo"]["score"] == pytest.approx(0.7)


def test_view_count_is_only_a_tiebreaker():
    ranked = rank_sources(
        GEM_LINGO,
        [
            yt("Overmono - Gem Lingo", "Fan", 231, 1_000),
            yt("Overmono - Gem Lingo", "Overmono", 231, None),
            yt("Overmono - Gem Lingo", "Big Channel", 231, 90_000_000),
        ],
    )
    assert [item["uploader"] for item in ranked] == ["Overmono", "Big Channel", "Fan"]
    assert ranked[1]["score"] - ranked[2]["score"] <= 0.04


def test_confidence_needs_a_margin_or_a_matching_official_runner_up():
    reuploads = rank_sources(LASSO, [yt("Phoenix - Lasso", "Fan", 168), yt("Phoenix - Lasso")])
    assert not any(item["confident"] for item in reuploads)

    official = rank_sources(
        LASSO, [yt("Lasso", "Phoenix - Topic", 168), sc("Lasso", "Phoenix", 167.9)]
    )
    assert official[0]["score"] - official[1]["score"] < 0.1
    assert official[0]["confident"] is True

    different = rank_sources(
        LASSO, [yt("Lasso", "Phoenix - Topic", 168), sc("Lasso (Demo)", "Phoenix", 199)]
    )
    assert different[0]["score"] - different[1]["score"] < 0.1
    assert different[0]["confident"] is False


def test_confidence_is_refused_for_penalised_best_candidates():
    extended = rank_sources(
        GEM_LINGO,
        [
            yt("Gem Lingo (Extended Mix) [Official Audio]", "Overmono - Topic", 300, 50_000_000),
            yt("Overmono - Gem Lingo", "Fan", 300),
        ],
    )
    assert extended[0]["score"] >= 0.75
    assert "extended mix not requested" in extended[0]["reasons"]
    assert extended[0]["confident"] is False

    candidates = [
        yt("Overmono - Gem Lingo [Audio]", "XL Recordings", 231, 50_000_000),
        yt("Overmono & Ruthven - Gem Lingo (Lyrics)", "Fan", 231),
    ]
    control = rank_sources(GEM_LINGO, candidates)
    assert control[0]["confident"] is True
    duo = {"artist": "Overmono, Ruthven", "title": "Gem Lingo", "version": ""}
    missing = rank_sources(duo, candidates)
    assert missing[0]["score"] >= 0.75
    assert missing[0]["score"] - missing[1]["score"] >= 0.1
    assert "credited artist missing" in missing[0]["reasons"]
    assert missing[0]["confident"] is False


def test_ranking_is_deterministic_and_returns_copies():
    entries = [dict(item) for item in LASSO_RESULTS]
    first = rank_sources(LASSO, entries)
    assert rank_sources(LASSO, list(reversed(entries))) == first
    assert entries == LASSO_RESULTS
    assert all("score" not in item for item in entries)
    assert first[0]["url"] == LASSO_RESULTS[0]["url"]
    assert rank_sources(LASSO, []) == []


def comment(text, start_time=None, likes=0, author="fan"):
    return {"text": text, "start_time": start_time, "like_count": likes, "author": author}


SOUNDCLOUD_COMMENTS = [
    comment("ID?", 483.2, 3),
    comment("What is the ID!!!!", 508.6, 1),
    comment("Woah Tracing Steps getting some attention? MMMM", 2971.6),
    comment("is this a remix of Rammstein's Sehnsucht!?", 27.4),
]
FAN_TRACKLIST = comment(
    "2:41 - gunk / 12:00 - freedom 2 / 16:05 - 🚀 / 26:00 - turn the page / 30:10 - so u kno / "
    "38:00 - hackney parrot",
    likes=40,
)
YOUTUBE_COMMENTS = [
    FAN_TRACKLIST,
    comment("That entire stretch starting @35:53 is almost transcendent", likes=12),
    comment("45:29 😎", likes=2),
    comment("the track at 1:02:30 is Bicep - Glue (Hammer edit)", likes=7),
    comment("yes Bicep - Glue edit", likes=2),
    comment("Yeah it's bicep - glue (hammer edit) 🔥", likes=1),
]


def labels(hints):
    return [hint["label"] for hint in hints]


def test_questions_and_prose_never_become_identities():
    assert comment_hints(SOUNDCLOUD_COMMENTS, None) == []
    assert comment_hints(SOUNDCLOUD_COMMENTS, 490) == []
    noise = [
        comment("🔥🔥🔥"),
        comment("banger!!"),
        comment("this is fire"),
        comment("track id??", 100),
        comment("12:00 - absolute banger"),
        comment("0:00 - ID - ID / 3:00 - track id?? / 6:00 - what is this"),
    ]
    assert comment_hints(noise, None) == []


def test_soundcloud_timed_comment_uses_its_position():
    comments = [*SOUNDCLOUD_COMMENTS, comment("@dj it's Overmono - So U Kno", 2990.0, likes=4)]
    hints = comment_hints(comments, 2971.6)
    assert hints == [
        {
            "artist": "Overmono",
            "title": "So U Kno",
            "label": "Overmono - So U Kno",
            "mentions": 1,
            "likes": 4,
            "evidence": ["@dj it's Overmono - So U Kno"],
            "at_seconds": 2990.0,
        }
    ]
    assert comment_hints(comments, 1000) == []


def test_fan_tracklist_titles_take_their_own_timestamps():
    everything = comment_hints(YOUTUBE_COMMENTS, None)
    assert labels(everything) == [
        "Bicep - Glue (Hammer edit)",
        "gunk",
        "freedom 2",
        "turn the page",
        "so u kno",
        "hackney parrot",
    ]
    parrot = comment_hints(YOUTUBE_COMMENTS, 38 * 60)
    assert parrot[0]["artist"] == "" and parrot[0]["title"] == "hackney parrot"
    assert parrot[0]["at_seconds"] == 2280.0 and parrot[0]["likes"] == 40
    assert "hackney parrot" in parrot[0]["evidence"][0]
    # "@35:53 is almost transcendent" and "45:29 😎" name nothing.
    assert labels(comment_hints(YOUTUBE_COMMENTS, 35 * 60 + 53)) == ["hackney parrot"]
    assert comment_hints(YOUTUBE_COMMENTS, 45 * 60 + 29) == []


def test_replies_back_a_timed_identity_and_are_counted():
    hints = comment_hints(YOUTUBE_COMMENTS, 3750)
    assert labels(hints) == ["Bicep - Glue (Hammer edit)"]
    assert hints[0]["mentions"] == 3
    assert hints[0]["likes"] == 10
    assert hints[0]["at_seconds"] == 3750.0
    assert hints[0]["evidence"][:2] == [
        "the track at 1:02:30 is Bicep - Glue (Hammer edit)",
        "yes Bicep - Glue edit",
    ]
    assert comment_hints(YOUTUBE_COMMENTS, 5000) == []


def test_identities_group_case_and_accent_insensitively():
    hints = comment_hints(
        [
            comment("Röyksopp - Here She Comes Again", 100, likes=2),
            comment("royksopp - here she comes again!!", 130, likes=3),
            comment("ROYKSOPP - HERE SHE COMES AGAIN", 5000),
        ],
        120,
    )
    assert len(hints) == 1
    assert hints[0]["label"] == "Röyksopp - Here She Comes Again"
    assert (hints[0]["mentions"], hints[0]["likes"], hints[0]["at_seconds"]) == (2, 5, 130.0)


def test_hints_rank_by_mentions_then_likes_then_closeness():
    hints = comment_hints(
        [
            comment("A - Far", 400, likes=1),
            comment("B - Near", 130, likes=1),
            comment("C - Liked", 380, likes=9),
            comment("D - Twice", 390),
            comment("d - twice", 395),
            comment("D - Twice, D - Twice", 396),
        ],
        120,
    )
    assert labels(hints) == ["D - Twice", "C - Liked", "B - Near", "A - Far"]
    assert hints[0]["mentions"] == 3


def test_list_heading_is_not_a_title_and_unrelated_replies_do_not_count():
    hints = comment_hints(
        [
            comment("Tracklist: 0:00 Overmono - Gem Lingo 3:30 so u kno"),
            comment("yes Bicep - Glue edit", likes=5),
        ],
        60,
    )
    assert [(hint["label"], hint["at_seconds"], hint["likes"]) for hint in hints] == [
        ("Overmono - Gem Lingo", 0.0, 0),
        ("so u kno", 210.0, 0),
    ]


def test_time_after_the_track_and_several_timestamps_in_one_comment():
    hints = comment_hints(
        [comment("Bicep - Glue 1:02:30, Overmono - So U Kno 1:05:00")],
        None,
    )
    assert [(hint["label"], hint["at_seconds"]) for hint in hints] == [
        ("Bicep - Glue", 3750.0),
        ("Overmono - So U Kno", 3900.0),
    ]


@pytest.mark.parametrize(
    ("text", "label", "seconds"),
    [
        ("Fred again.. - Delilah at 4:20 omg", "Fred again.. - Delilah", 260.0),
        ("ID at 12:30 is Mall Grab - Spirit Wave I think?", "Mall Grab - Spirit Wave", 750.0),
        ("Mr. Oizo - Flat Beat @ 10:00", "Mr. Oizo - Flat Beat", 600.0),
        ("4:20 Overmono - Gem Lingo (ovr now) 🔥🔥", "Overmono - Gem Lingo (ovr now)", 260.0),
    ],
)
def test_mentions_are_trimmed_to_what_was_written(text, label, seconds):
    (hint,) = comment_hints([comment(text)], None)
    assert (hint["label"], hint["at_seconds"]) == (label, seconds)


def test_comment_work_is_bounded():
    ignored = [comment("ID?", 10)] * 2000 + [comment("Overmono - Gem Lingo", 10)]
    assert comment_hints(ignored, None) == []
    many = [comment(f"Artist {n} - Title {n}", 10 + n) for n in range(40)]
    assert len(comment_hints(many, None)) == 20
    long = comment("Overmono - Gem Lingo " + "x" * 10_000)
    assert len(comment_hints([long], None)[0]["evidence"][0]) <= 102


def round_trip(text):
    items, skipped = parse_tracklist(text)
    assert skipped == []
    return [(item.kind, item.artist, item.title, item.label, item.timestamp) for item in items]


def test_description_tracklist_with_header_and_timestamps():
    description = """Overmono live at Printworks London, March 2024.

Tracklist:
00:00 Overmono - Gem Lingo (ovr now)
3:51 Bicep - Glue (Hammer Edit) [Ninja Tune]
07:20 - ID - ID
1:02:30 Four Tet - Baby

Follow Overmono: https://instagram.com/overmono
#overmono #printworks"""
    text = tracklist_from_source(description, [])
    assert text.splitlines() == [
        "[00:00] Overmono - Gem Lingo (ovr now)",
        "[03:51] Bicep - Glue (Hammer Edit) [Ninja Tune]",
        "[07:20] ID - ID",
        "[1:02:30] Four Tet - Baby",
    ]
    assert round_trip(text) == [
        ("named", "Overmono", "Gem Lingo (ovr now)", None, None),
        ("named", "Bicep", "Glue (Hammer Edit)", None, None),
        ("unknown", None, None, "ID - ID", "07:20"),
        ("named", "Four Tet", "Baby", None, None),
    ]


def test_chapters_win_when_there_are_at_least_three():
    chapters = [
        {"start_time": 231.5, "title": "Bicep - Glue"},
        {"start_time": 0.0, "title": "Intro"},
        {"start_time": 30.0, "title": "01. Overmono - Gem Lingo"},
        {"start_time": 3750.0, "title": "Four Tet - Baby"},
    ]
    text = tracklist_from_source("Tracklist:\n00:00 Someone - Else\n01:00 Other - One", chapters)
    assert text.splitlines() == [
        "[00:30] Overmono - Gem Lingo",
        "[03:51] Bicep - Glue",
        "[1:02:30] Four Tet - Baby",
    ]
    assert [item[1:3] for item in round_trip(text)] == [
        ("Overmono", "Gem Lingo"),
        ("Bicep", "Glue"),
        ("Four Tet", "Baby"),
    ]
    two = tracklist_from_source("00:00 Someone - Else\n01:00 Other - One", chapters[:2])
    assert two.splitlines() == ["[00:00] Someone - Else", "[01:00] Other - One"]


def test_promo_junk_is_dropped_from_descriptions():
    description = """Stream / buy 'Gem Lingo' here: https://overmono.lnk.to/gemlingo
Out now on XL Recordings
Subscribe for more - https://youtube.com/@xlrecordings
Instagram - @overmono
Tracklist
1. Overmono - Gem Lingo
2. Bicep - Glue
3. Four Tet - Baby (Extended Mix)
℗ 2024 XL Recordings Ltd
Booking - bookings@agency.example
#overmono #ukgarage"""
    text = tracklist_from_source(description, [])
    assert text.splitlines() == [
        "Overmono - Gem Lingo",
        "Bicep - Glue",
        "Four Tet - Baby (Extended Mix)",
    ]
    assert [item[1:3] for item in round_trip(text)] == [
        ("Overmono", "Gem Lingo"),
        ("Bicep", "Glue"),
        ("Four Tet", "Baby (Extended Mix)"),
    ]


def test_trailing_times_move_to_the_front_and_empty_sources_give_nothing():
    text = tracklist_from_source("Phoenix - Lasso - 2:41\nBicep - Glue (12:00)\nwe love you", [])
    assert text.splitlines() == ["[02:41] Phoenix - Lasso", "[12:00] Bicep - Glue"]
    assert tracklist_from_source("", []) == ""
    assert tracklist_from_source("Thanks for listening! https://example.test", []) == ""
