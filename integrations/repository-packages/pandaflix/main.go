package main

import (
    "encoding/json"
    "fmt"
    "net/http"
    "os"
    "time"

    "github.com/MADPANDA3D/pandaflix/core"
    "github.com/MADPANDA3D/pandaflix/core/providers"
)

func main() {
    var args struct { S string `json:"s"`; URL string `json:"urlStr"` }
    if err := json.NewDecoder(os.Stdin).Decode(&args); err != nil { panic(err) }
    var result any
    switch os.Args[1] {
    case "pandaflix__episode_range":
        episodes, err := core.ParseEpisodeRange(args.S)
        if err != nil { panic(err) }
        result = map[string]any{"episodes": episodes}
    case "pandaflix__youtube_playback_plan":
        provider := providers.NewYouTube(&http.Client{Timeout: 8*time.Second})
        id, err := provider.GetMediaID(args.URL)
        if err != nil { panic(err) }
        link, err := provider.GetLink(id)
        if err != nil { panic(err) }
        result = map[string]any{"media_id": id, "playback_url": link, "requires_player": true}
    case "pandaflix__watch_history":
        db, err := core.OpenHistory()
        if err != nil { panic(err) }
        defer db.Close()
        shows, err := db.ListShows()
        if err != nil { panic(err) }
        rows := []map[string]any{}
        for _, show := range shows {
            if len(rows) == 100 { break }
            rows = append(rows, map[string]any{"title": show.Title, "url": show.URL, "season": show.Season, "episode": show.Episode})
        }
        result = map[string]any{"shows": rows}
    default: panic(fmt.Errorf("unknown operation"))
    }
    if err := json.NewEncoder(os.Stdout).Encode(result); err != nil { panic(err) }
}
