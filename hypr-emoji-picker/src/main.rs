use std::collections::{HashMap, HashSet};
use std::env;
use std::fs;
use std::io::Write;
use std::path::PathBuf;
use std::process::{Command, ExitCode, Stdio};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use emojis::{Emoji, Group};
use serde::{Deserialize, Serialize};

const DEFAULT_LIMIT: usize = 80;
const RESTORE_DELAY_MS: u64 = 250;

#[derive(Debug, Clone, Serialize)]
struct EmojiRow {
    emoji: String,
    name: String,
    group: String,
    shortcodes: Vec<String>,
    keywords: Vec<String>,
    favorite: bool,
    recent_rank: Option<usize>,
    recent_count: u32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct RecentEntry {
    emoji: String,
    count: u32,
    last_used: u64,
}

#[derive(Debug, Default)]
struct State {
    favorites: Vec<String>,
    recents: Vec<RecentEntry>,
}

#[derive(Debug)]
enum CommandKind {
    Search {
        query: String,
        json: bool,
        limit: usize,
    },
    Recent {
        json: bool,
    },
    Favorite(FavoriteCommand),
    Insert {
        emoji: String,
        copy_only: bool,
    },
    PickFuzzel,
    Help,
}

#[derive(Debug)]
enum FavoriteCommand {
    List { json: bool },
    Add { emoji: String },
    Remove { emoji: String },
    Toggle { emoji: String },
}

fn main() -> ExitCode {
    match run() {
        Ok(()) => ExitCode::SUCCESS,
        Err(err) => {
            eprintln!("hypr-emoji-picker: {err}");
            ExitCode::from(1)
        }
    }
}

fn run() -> Result<(), String> {
    match parse_args(env::args().skip(1).collect())? {
        CommandKind::Search { query, json, limit } => {
            let state = load_state();
            let rows = search_rows(&query, limit, &state);
            emit_rows(&rows, json)?;
        }
        CommandKind::Recent { json } => {
            let state = load_state();
            let rows = recent_rows(&state);
            emit_rows(&rows, json)?;
        }
        CommandKind::Favorite(cmd) => handle_favorite(cmd)?,
        CommandKind::Insert { emoji, copy_only } => insert_emoji(&emoji, copy_only)?,
        CommandKind::PickFuzzel => pick_fuzzel()?,
        CommandKind::Help => print_usage(),
    }
    Ok(())
}

fn parse_args(args: Vec<String>) -> Result<CommandKind, String> {
    let Some(cmd) = args.first().map(String::as_str) else {
        return Ok(CommandKind::Help);
    };
    match cmd {
        "search" => {
            let mut json = false;
            let mut limit = DEFAULT_LIMIT;
            let mut query = Vec::new();
            let mut i = 1;
            while i < args.len() {
                match args[i].as_str() {
                    "--json" => json = true,
                    "--limit" => {
                        i += 1;
                        let raw = args.get(i).ok_or("--limit requires a number")?;
                        limit = raw.parse().map_err(|_| "--limit must be a number")?;
                    }
                    other => query.push(other.to_string()),
                }
                i += 1;
            }
            Ok(CommandKind::Search {
                query: query.join(" "),
                json,
                limit,
            })
        }
        "recent" => Ok(CommandKind::Recent {
            json: args.iter().any(|a| a == "--json"),
        }),
        "favorite" => parse_favorite(args),
        "insert" => {
            let copy_only = args.iter().any(|a| a == "--copy-only");
            let emoji = args
                .iter()
                .skip(1)
                .find(|arg| arg.as_str() != "--copy-only")
                .ok_or("insert requires an emoji")?
                .to_string();
            Ok(CommandKind::Insert { emoji, copy_only })
        }
        "pick-fuzzel" => Ok(CommandKind::PickFuzzel),
        "help" | "-h" | "--help" => Ok(CommandKind::Help),
        other => Err(format!("unknown command '{other}'\n\n{}", usage_text())),
    }
}

fn parse_favorite(args: Vec<String>) -> Result<CommandKind, String> {
    let Some(sub) = args.get(1).map(String::as_str) else {
        return Ok(CommandKind::Favorite(FavoriteCommand::List {
            json: args.iter().any(|a| a == "--json"),
        }));
    };
    let cmd = match sub {
        "list" => FavoriteCommand::List {
            json: args.iter().any(|a| a == "--json"),
        },
        "add" => FavoriteCommand::Add {
            emoji: args
                .get(2)
                .ok_or("favorite add requires an emoji")?
                .to_string(),
        },
        "remove" | "rm" => FavoriteCommand::Remove {
            emoji: args
                .get(2)
                .ok_or("favorite remove requires an emoji")?
                .to_string(),
        },
        "toggle" => FavoriteCommand::Toggle {
            emoji: args
                .get(2)
                .ok_or("favorite toggle requires an emoji")?
                .to_string(),
        },
        other => return Err(format!("unknown favorite command '{other}'")),
    };
    Ok(CommandKind::Favorite(cmd))
}

fn print_usage() {
    println!("{}", usage_text());
}

fn usage_text() -> &'static str {
    "usage: hypr-emoji-picker <command>\n\
     \n\
     search <query> [--json] [--limit N]   search emoji metadata\n\
     recent [--json]                       print recently inserted emoji\n\
     favorite list [--json]                print favorites\n\
     favorite add <emoji>                  add a favorite\n\
     favorite remove <emoji>               remove a favorite\n\
     favorite toggle <emoji>               toggle favorite state\n\
     insert <emoji> [--copy-only]          paste emoji, or only copy and mark recent\n\
     pick-fuzzel                           temporary fuzzel picker UI"
}

fn handle_favorite(cmd: FavoriteCommand) -> Result<(), String> {
    let mut state = load_state();
    match cmd {
        FavoriteCommand::List { json } => {
            let rows = favorite_rows(&state);
            emit_rows(&rows, json)?;
        }
        FavoriteCommand::Add { emoji } => {
            normalize_known_emoji(&emoji)?;
            if !state.favorites.iter().any(|e| e == &emoji) {
                state.favorites.insert(0, emoji.clone());
                save_favorites(&state.favorites)?;
            }
            println!("{emoji}");
        }
        FavoriteCommand::Remove { emoji } => {
            state.favorites.retain(|e| e != &emoji);
            save_favorites(&state.favorites)?;
            println!("{emoji}");
        }
        FavoriteCommand::Toggle { emoji } => {
            normalize_known_emoji(&emoji)?;
            if state.favorites.iter().any(|e| e == &emoji) {
                state.favorites.retain(|e| e != &emoji);
            } else {
                state.favorites.insert(0, emoji.clone());
            }
            save_favorites(&state.favorites)?;
            println!("{emoji}");
        }
    }
    Ok(())
}

fn search_rows(query: &str, limit: usize, state: &State) -> Vec<EmojiRow> {
    let favorites: HashSet<&str> = state.favorites.iter().map(String::as_str).collect();
    let recent_rank = recent_rank_map(state);
    let recent_count = recent_count_map(state);
    let trimmed = query.trim();
    let query_tokens = tokenize(trimmed);

    let mut scored = Vec::new();
    for emoji in emojis::iter() {
        let row = row_for_emoji(emoji, &favorites, &recent_rank, &recent_count);
        let score = if trimmed.is_empty() {
            base_score(&row)
        } else {
            match_score(&row, trimmed, &query_tokens)
        };
        if score > 0 {
            scored.push((score, row));
        }
    }

    scored.sort_by(|(a_score, a), (b_score, b)| {
        b_score
            .cmp(a_score)
            .then_with(|| b.favorite.cmp(&a.favorite))
            .then_with(|| {
                a.recent_rank
                    .unwrap_or(usize::MAX)
                    .cmp(&b.recent_rank.unwrap_or(usize::MAX))
            })
            .then_with(|| a.name.cmp(&b.name))
    });
    scored.into_iter().map(|(_, row)| row).take(limit).collect()
}

fn base_score(row: &EmojiRow) -> i32 {
    let mut score = 1;
    if row.favorite {
        score += 10_000;
    }
    if let Some(rank) = row.recent_rank {
        score += 5_000 - rank.min(100) as i32;
    }
    score
}

fn match_score(row: &EmojiRow, raw_query: &str, tokens: &[String]) -> i32 {
    if row.emoji == raw_query {
        return 50_000;
    }
    let mut haystacks = Vec::new();
    haystacks.push(row.name.to_ascii_lowercase());
    haystacks.push(row.group.to_ascii_lowercase());
    haystacks.extend(
        row.shortcodes
            .iter()
            .map(|s| s.replace('_', " ").to_ascii_lowercase()),
    );
    haystacks.extend(row.keywords.iter().map(|s| s.to_ascii_lowercase()));

    let mut score = 0;
    for token in tokens {
        let mut token_score = 0;
        for hay in &haystacks {
            if hay == token {
                token_score = token_score.max(900);
            } else if hay.split_whitespace().any(|part| part == token) {
                token_score = token_score.max(600);
            } else if hay.contains(token) {
                token_score = token_score.max(300);
            }
        }
        if token_score == 0 {
            return 0;
        }
        score += token_score;
    }
    if row.favorite {
        score += 100;
    }
    if let Some(rank) = row.recent_rank {
        score += 50 - rank.min(50) as i32;
    }
    score
}

fn recent_rows(state: &State) -> Vec<EmojiRow> {
    let favorites: HashSet<&str> = state.favorites.iter().map(String::as_str).collect();
    let recent_rank = recent_rank_map(state);
    let recent_count = recent_count_map(state);
    state
        .recents
        .iter()
        .filter_map(|entry| emojis::get(&entry.emoji))
        .map(|emoji| row_for_emoji(emoji, &favorites, &recent_rank, &recent_count))
        .collect()
}

fn favorite_rows(state: &State) -> Vec<EmojiRow> {
    let favorites: HashSet<&str> = state.favorites.iter().map(String::as_str).collect();
    let recent_rank = recent_rank_map(state);
    let recent_count = recent_count_map(state);
    state
        .favorites
        .iter()
        .filter_map(|value| emojis::get(value))
        .map(|emoji| row_for_emoji(emoji, &favorites, &recent_rank, &recent_count))
        .collect()
}

fn row_for_emoji(
    emoji: &'static Emoji,
    favorites: &HashSet<&str>,
    recent_rank: &HashMap<String, usize>,
    recent_count: &HashMap<String, u32>,
) -> EmojiRow {
    let value = emoji.as_str().to_string();
    let shortcodes: Vec<String> = emoji.shortcodes().map(ToOwned::to_owned).collect();
    EmojiRow {
        emoji: value.clone(),
        name: emoji.name().to_string(),
        group: group_name(emoji.group()).to_string(),
        keywords: keywords_for(emoji, &shortcodes),
        shortcodes,
        favorite: favorites.contains(value.as_str()),
        recent_rank: recent_rank.get(&value).copied(),
        recent_count: recent_count.get(&value).copied().unwrap_or(0),
    }
}

fn emit_rows(rows: &[EmojiRow], json: bool) -> Result<(), String> {
    if json {
        println!(
            "{}",
            serde_json::to_string_pretty(rows).map_err(|e| e.to_string())?
        );
    } else {
        for row in rows {
            println!("{}", display_line(row));
        }
    }
    Ok(())
}

fn display_line(row: &EmojiRow) -> String {
    let star = if row.favorite { "★ " } else { "  " };
    let shortcode = row
        .shortcodes
        .first()
        .map(|s| format!(" :{s}:"))
        .unwrap_or_default();
    let recent = row
        .recent_rank
        .map(|rank| format!(" · recent #{}", rank + 1))
        .unwrap_or_default();
    format!(
        "{star}{}  {}{} · {}{}",
        row.emoji, row.name, shortcode, row.group, recent
    )
}

fn pick_fuzzel() -> Result<(), String> {
    let state = load_state();
    let rows = search_rows("", 350, &state);
    let mut child = Command::new("fuzzel")
        .args([
            "--dmenu", "--prompt", "emoji  ", "--lines", "12", "--width", "48",
        ])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .spawn()
        .map_err(|e| format!("failed to start fuzzel: {e}"))?;
    {
        let stdin = child.stdin.as_mut().ok_or("failed to open fuzzel stdin")?;
        for row in &rows {
            writeln!(stdin, "{}", display_line(row)).map_err(|e| e.to_string())?;
        }
    }
    let output = child.wait_with_output().map_err(|e| e.to_string())?;
    if !output.status.success() {
        return Ok(());
    }
    let selection = String::from_utf8_lossy(&output.stdout);
    let Some(emoji) = parse_selected_emoji(&selection) else {
        return Ok(());
    };
    insert_emoji(&emoji, false)
}

fn parse_selected_emoji(selection: &str) -> Option<String> {
    let mut parts = selection.split_whitespace();
    let first = parts.next()?;
    if first == "★" {
        parts.next().map(ToOwned::to_owned)
    } else {
        Some(first.to_string())
    }
}

fn insert_emoji(value: &str, copy_only: bool) -> Result<(), String> {
    normalize_known_emoji(value)?;
    if copy_only {
        write_clipboard(value)?;
        update_recent(value)?;
        println!("{value}");
        return Ok(());
    }
    let previous = read_clipboard_text();
    write_clipboard(value)?;
    let pasted = Command::new("hyprctl")
        .args(["dispatch", "sendshortcut", "CTRL,V,activewindow"])
        .status()
        .map(|s| s.success())
        .unwrap_or(false);
    if !pasted {
        let typed = Command::new("wtype")
            .arg(value)
            .status()
            .map(|s| s.success())
            .unwrap_or(false);
        if !typed {
            return Err("failed to paste with hyprctl and failed to type with wtype".into());
        }
    }
    std::thread::sleep(Duration::from_millis(RESTORE_DELAY_MS));
    if let Some(text) = previous {
        let _ = write_clipboard(&text);
    }
    update_recent(value)?;
    println!("{value}");
    Ok(())
}

fn read_clipboard_text() -> Option<String> {
    let output = Command::new("wl-paste")
        .args(["--no-newline"])
        .output()
        .ok()?;
    output
        .status
        .success()
        .then(|| String::from_utf8_lossy(&output.stdout).to_string())
}

fn write_clipboard(text: &str) -> Result<(), String> {
    let mut child = Command::new("wl-copy")
        .stdin(Stdio::piped())
        .spawn()
        .map_err(|e| format!("failed to start wl-copy: {e}"))?;
    if let Some(stdin) = child.stdin.as_mut() {
        stdin
            .write_all(text.as_bytes())
            .map_err(|e| e.to_string())?;
    }
    let status = child.wait().map_err(|e| e.to_string())?;
    status
        .success()
        .then_some(())
        .ok_or("wl-copy failed".into())
}

fn update_recent(value: &str) -> Result<(), String> {
    let mut state = load_state();
    let now = now();
    if let Some(entry) = state.recents.iter_mut().find(|entry| entry.emoji == value) {
        entry.count = entry.count.saturating_add(1);
        entry.last_used = now;
    } else {
        state.recents.insert(
            0,
            RecentEntry {
                emoji: value.to_string(),
                count: 1,
                last_used: now,
            },
        );
    }
    state
        .recents
        .sort_by_key(|entry| std::cmp::Reverse(entry.last_used));
    state.recents.truncate(80);
    save_recents(&state.recents)
}

fn normalize_known_emoji(value: &str) -> Result<&'static Emoji, String> {
    emojis::get(value).ok_or_else(|| format!("unknown emoji '{value}'"))
}

fn load_state() -> State {
    State {
        favorites: read_json(favorites_path()).unwrap_or_default(),
        recents: read_json(recents_path()).unwrap_or_default(),
    }
}

fn read_json<T: for<'de> Deserialize<'de>>(path: PathBuf) -> Option<T> {
    fs::read_to_string(path)
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
}

fn save_favorites(favorites: &[String]) -> Result<(), String> {
    write_json(favorites_path(), favorites)
}

fn save_recents(recents: &[RecentEntry]) -> Result<(), String> {
    write_json(recents_path(), recents)
}

fn write_json<T: Serialize + ?Sized>(path: PathBuf, value: &T) -> Result<(), String> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|e| format!("create {}: {e}", parent.display()))?;
    }
    let bytes = serde_json::to_vec_pretty(value).map_err(|e| e.to_string())?;
    let tmp = path.with_extension("tmp");
    fs::write(&tmp, bytes).map_err(|e| format!("write {}: {e}", tmp.display()))?;
    fs::rename(&tmp, &path).map_err(|e| format!("rename {}: {e}", path.display()))
}

fn state_dir() -> PathBuf {
    if let Ok(dir) = env::var("HYPR_EMOJI_PICKER_STATE") {
        return PathBuf::from(dir);
    }
    let base = env::var("XDG_STATE_HOME")
        .ok()
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| {
            let home = env::var("HOME").unwrap_or_else(|_| "/tmp".into());
            format!("{home}/.local/state")
        });
    PathBuf::from(base).join("hypr-emoji-picker")
}

fn favorites_path() -> PathBuf {
    state_dir().join("favorites.json")
}

fn recents_path() -> PathBuf {
    state_dir().join("recents.json")
}

fn recent_rank_map(state: &State) -> HashMap<String, usize> {
    state
        .recents
        .iter()
        .enumerate()
        .map(|(rank, entry)| (entry.emoji.clone(), rank))
        .collect()
}

fn recent_count_map(state: &State) -> HashMap<String, u32> {
    state
        .recents
        .iter()
        .map(|entry| (entry.emoji.clone(), entry.count))
        .collect()
}

fn now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
}

fn tokenize(s: &str) -> Vec<String> {
    s.split(|c: char| !(c.is_alphanumeric() || c == '_' || c == '-'))
        .filter(|part| !part.is_empty())
        .map(|part| part.replace(['_', '-'], " ").to_ascii_lowercase())
        .flat_map(|part| {
            part.split_whitespace()
                .map(ToOwned::to_owned)
                .collect::<Vec<_>>()
        })
        .collect()
}

fn group_name(group: Group) -> &'static str {
    match group {
        Group::SmileysAndEmotion => "Smileys & Emotion",
        Group::PeopleAndBody => "People & Body",
        Group::AnimalsAndNature => "Animals & Nature",
        Group::FoodAndDrink => "Food & Drink",
        Group::TravelAndPlaces => "Travel & Places",
        Group::Activities => "Activities",
        Group::Objects => "Objects",
        Group::Symbols => "Symbols",
        Group::Flags => "Flags",
    }
}

fn keywords_for(emoji: &Emoji, shortcodes: &[String]) -> Vec<String> {
    let mut out = Vec::new();
    out.extend(shortcodes.iter().flat_map(|s| tokenize(s)));
    out.extend(tokenize(emoji.name()));
    out.extend(tokenize(group_name(emoji.group())));
    for kw in extra_keywords(emoji.as_str(), emoji.name()) {
        out.push(kw.to_string());
    }
    out.sort();
    out.dedup();
    out
}

fn extra_keywords(value: &str, name: &str) -> &'static [&'static str] {
    match value {
        "😂" => &["lol", "lmao", "laugh", "cry laughing"],
        "🤣" => &["rofl", "lol", "laugh"],
        "❤️" | "♥️" | "💕" | "💖" => &["love", "heart"],
        "👍" => &["like", "yes", "approve"],
        "👎" => &["dislike", "no"],
        "🙏" => &["thanks", "thank you", "please", "pray"],
        "🔥" => &["fire", "lit", "hot"],
        "✨" => &["sparkle", "magic"],
        "🎉" => &["party", "celebrate", "congrats"],
        "🚀" => &["ship", "launch"],
        _ if name.contains("face") => &["face"],
        _ => &[],
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn search_finds_common_alias() {
        let rows = search_rows("lmao", 5, &State::default());
        assert_eq!(rows.first().map(|r| r.emoji.as_str()), Some("😂"));
    }

    #[test]
    fn favorite_sorts_before_plain_results() {
        let state = State {
            favorites: vec!["🚀".into()],
            recents: vec![],
        };
        let rows = search_rows("rocket", 5, &state);
        assert!(rows.first().map(|r| r.favorite).unwrap_or(false));
    }

    #[test]
    fn selected_fuzzel_line_extracts_emoji() {
        assert_eq!(
            parse_selected_emoji("★ 😂  face with tears\n"),
            Some("😂".into())
        );
        assert_eq!(parse_selected_emoji("  🚀  rocket\n"), Some("🚀".into()));
    }
}
