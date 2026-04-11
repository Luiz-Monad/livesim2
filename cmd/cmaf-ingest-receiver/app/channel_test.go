package app

import (
	"context"
	"os"
	"testing"

	mx "github.com/Dash-Industry-Forum/livesim2/pkg/mpd"
	m "github.com/Eyevinn/dash-mpd/mpd"
	"github.com/Eyevinn/mp4ff/bits"
	"github.com/Eyevinn/mp4ff/mp4"
	"github.com/stretchr/testify/assert"
)

func TestAddVideoInit(t *testing.T) {
	videoData, err := os.ReadFile("testdata/video/init.cmfv")
	assert.NoError(t, err)
	chName, chDir := "testpic", "testdir/testpic"
	ctx := context.TODO()
	chCfg := ChannelConfig{
		Name:                  chName,
		TimeShiftBufferDepthS: 60,
	}
	ch := newChannel(ctx, chCfg, chDir)
	strm := stream{
		chName:    chName,
		chDir:     chDir,
		trName:    "video",
		ext:       "cmfv",
		mediaType: "video",
	}
	sr := bits.NewFixedSliceReader(videoData)
	decFile, err := mp4.DecodeFileSR(sr)
	assert.NoError(t, err)
	init := decFile.Init
	err = ch.addInitDataAndUpdateTimescale(strm, init)
	assert.NoError(t, err)
	assert.Equal(t, m.DateTime("1970-01-01T00:00:00Z"), ch.mpd.AvailabilityStartTime)
	p := ch.mpd.Periods[0]
	assert.Equal(t, 1, len(p.AdaptationSets))
	asSet := p.AdaptationSets[0]
	assert.Equal(t, uint32(1), *asSet.Id)
	assert.Equal(t, m.RFC6838ContentTypeType("video"), asSet.ContentType)
	assert.Equal(t, "video/mp4", asSet.MimeType)
	assert.Equal(t, "und", asSet.Lang)
	st := mx.SegmentTemplate(asSet)
	assert.NotNil(t, st)
	assert.Equal(t, "$RepresentationID$/init.cmfv", st.Initialization)
	assert.Equal(t, "$RepresentationID$/$Number$.cmfv", st.Media)
	assert.Equal(t, uint32(90000), *st.Timescale)
	rep := asSet.Representations[0]
	assert.Equal(t, "video", rep.Id)
	assert.Equal(t, 800000, int(rep.Bandwidth))
	assert.Equal(t, "avc1.64001E", rep.Codecs)
}

func TestVideoDataFromInit(t *testing.T) {
	videoData, err := os.ReadFile("testdata/video/init.cmfv")
	assert.NoError(t, err)
	chName, chDir := "testpic", "testdir/testpic"
	ctx := context.TODO()
	chCfg := ChannelConfig{
		Name:                  chName,
		TimeShiftBufferDepthS: 60,
	}
	ch := newChannel(ctx, chCfg, chDir)
	strm := stream{
		chName:    chName,
		chDir:     chDir,
		trName:    "video",
		ext:       "cmfv",
		mediaType: "video",
	}
	sr := bits.NewFixedSliceReader(videoData)
	decFile, err := mp4.DecodeFileSR(sr)
	assert.NoError(t, err)
	init := decFile.Init
	err = ch.addInitDataAndUpdateTimescale(strm, init)
	assert.NoError(t, err)
	assert.Equal(t, m.DateTime("1970-01-01T00:00:00Z"), ch.mpd.AvailabilityStartTime)
	p := ch.mpd.Periods[0]
	assert.Equal(t, 1, len(p.AdaptationSets))
	asSet := p.AdaptationSets[0]
	assert.Equal(t, uint32(1), *asSet.Id)
	assert.Equal(t, m.RFC6838ContentTypeType("video"), asSet.ContentType)
	assert.Equal(t, "video/mp4", asSet.MimeType)
	st := mx.SegmentTemplate(asSet)
	assert.NotNil(t, st)
	assert.Equal(t, "$RepresentationID$/init.cmfv", st.Initialization)
	assert.Equal(t, "$RepresentationID$/$Number$.cmfv", st.Media)
	assert.Equal(t, uint32(90000), *st.Timescale)
	rep := asSet.Representations[0]
	assert.Equal(t, "video", rep.Id)
	assert.Equal(t, 800000, int(rep.Bandwidth))
	assert.Equal(t, "avc1.64001E", rep.Codecs)
	assert.Equal(t, 640, int(rep.Width))
	assert.Equal(t, 350, int(rep.Height))
}

func TestGetLang(t *testing.T) {
	cases := []struct {
		mdhdLang string
		elngLang string
		expected string
	}{
		{mdhdLang: "```", elngLang: "", expected: "und"},
		{mdhdLang: "se`", elngLang: "", expected: "se"},
		{mdhdLang: "swe", elngLang: "se", expected: "se"},
	}
	for _, c := range cases {
		mdia := mp4.MdiaBox{}
		mdhd := mp4.MdhdBox{}
		mdhd.SetLanguage(c.mdhdLang)
		mdia.AddChild(&mdhd)
		if c.elngLang != "" {
			elng := mp4.ElngBox{}
			elng.Language = c.elngLang
			mdia.AddChild(&elng)
		}
		gotLang := getLang(&mdia)
		assert.Equal(t, c.expected, gotLang)
	}
}

func TestAddInitTwice(t *testing.T) {
	videoData, err := os.ReadFile("testdata/video/init.cmfv")
	assert.NoError(t, err)
	chName, chDir := "testpic", "testdir/testpic"
	ctx := context.TODO()
	chCfg := ChannelConfig{
		Name:                  chName,
		TimeShiftBufferDepthS: 60,
	}
	ch := newChannel(ctx, chCfg, chDir)
	strm := stream{
		chName:    chName,
		chDir:     chDir,
		trName:    "video",
		ext:       "cmfv",
		mediaType: "video",
	}
	sr := bits.NewFixedSliceReader(videoData)
	decFile, err := mp4.DecodeFileSR(sr)
	assert.NoError(t, err)
	init := decFile.Init

	err = ch.addInitDataAndUpdateTimescale(strm, init)
	assert.NoError(t, err)

	p := ch.mpd.Periods[0]
	assert.Equal(t, 1, len(p.AdaptationSets))
	asSet := p.AdaptationSets[0]
	assert.Equal(t, 1, len(asSet.Representations))

	err = ch.addInitDataAndUpdateTimescale(strm, init)
	assert.NoError(t, err)

	assert.Equal(t, 1, len(p.AdaptationSets))
	asSet = p.AdaptationSets[0]
	assert.Equal(t, 1, len(asSet.Representations), "sending init segment twice should not create duplicate Representations")
}

func TestAddTwoAudioTracks(t *testing.T) {
	audioData, err := os.ReadFile("testdata/awsMediaLiveScte35/audio/init.cmfa")
	assert.NoError(t, err)
	chName, chDir := "testaudio", "testdir/testaudio"
	ctx := context.TODO()
	chCfg := ChannelConfig{
		Name:                  chName,
		TimeShiftBufferDepthS: 60,
	}
	ch := newChannel(ctx, chCfg, chDir)

	sr := bits.NewFixedSliceReader(audioData)
	decFile, err := mp4.DecodeFileSR(sr)
	assert.NoError(t, err)
	init := decFile.Init

	strm1 := stream{
		chName:    chName,
		chDir:     chDir,
		trName:    "audio1",
		ext:       "cmfa",
		mediaType: "audio",
	}
	err = ch.addInitDataAndUpdateTimescale(strm1, init)
	assert.NoError(t, err)

	strm2 := stream{
		chName:    chName,
		chDir:     chDir,
		trName:    "audio2",
		ext:       "cmfa",
		mediaType: "audio",
	}
	err = ch.addInitDataAndUpdateTimescale(strm2, init)
	assert.NoError(t, err)

	p := ch.mpd.Periods[0]
	assert.Equal(t, 2, len(p.AdaptationSets), "two audio tracks each one will be on their own AdaptationSet")
	asSet0 := p.AdaptationSets[0]
	assert.Equal(t, 1, len(asSet0.Representations), "audio tracks should have only one Representation")
	asSet1 := p.AdaptationSets[1]
	assert.Equal(t, 1, len(asSet1.Representations), "audio tracks should have only one Representation")
	assert.NotNil(t, asSet0.SegmentTemplate)
	assert.NotNil(t, asSet0.SegmentTemplate.Timescale, "Timescale should not be nil for track")
	assert.NotNil(t, asSet1.SegmentTemplate)
	assert.NotNil(t, asSet1.SegmentTemplate.Timescale, "Timescale should not be nil for track")
}
