// Copyright 2023, DASH-Industry Forum. All rights reserved.
// Use of this source code is governed by a BSD-style
// license that can be found in the LICENSE.md file.

package app

import (
	"log/slog"
	"os"
	"path"
	"path/filepath"
	"testing"

	m "github.com/Eyevinn/dash-mpd/mpd"
	"github.com/stretchr/testify/require"
	"github.com/wwmoraes/go-rwfs"
)

type wantedAssetData struct {
	nrReps         int
	loopDurationMS int
}

type wantedRepData struct {
	nrSegs         int
	initURI        string
	mpdTimescale   int // SegmentTemplate timescale
	mediaTimescale int
	duration       int
	editListOffset int64 // Offset of the elst box in the init segment
}

func TestLoadAsset(t *testing.T) {
	logger := slog.Default()
	testCases := []struct {
		desc         string
		assetPath    string
		segmentEndNr uint32
		ad           wantedAssetData
		rds          map[string]wantedRepData
	}{
		{
			desc:      "CTA-Wave AAC with editlist",
			assetPath: "WAVE/av",
			ad: wantedAssetData{
				nrReps:         2,
				loopDurationMS: 8000,
			},
			rds: map[string]wantedRepData{
				"video25fps": {
					nrSegs:         4,
					initURI:        "video25fps/init.mp4",
					mpdTimescale:   12_800,
					mediaTimescale: 12_800,
					duration:       102_400,
				},
				"aac": {
					nrSegs:         5, // To get longer duration than video
					initURI:        "aac/init.mp4",
					mpdTimescale:   48_000,
					mediaTimescale: 48_000,
					duration:       476160, // 9.92s which is fine since longer than video
					editListOffset: 2048,
				},
			},
		},
		{
			desc:         "testpic_2s",
			assetPath:    "testpic_2s",
			segmentEndNr: 0, // Will not be used
			ad: wantedAssetData{
				nrReps:         5,
				loopDurationMS: 8000,
			},
			rds: map[string]wantedRepData{
				"V300": {
					nrSegs:         4,
					initURI:        "V300/init.mp4",
					mpdTimescale:   1,
					mediaTimescale: 90_000,
					duration:       720_000,
				},
				"A48": {
					nrSegs:         4,
					initURI:        "A48/init.mp4",
					mpdTimescale:   1,
					mediaTimescale: 48_000,
					duration:       384_000,
				},
			},
		},
		{
			desc:         "testpic_2s with endNumber == 2",
			assetPath:    "testpic_2s",
			segmentEndNr: 2, // Shorten representations to 2 segments via SegmentTemplate,
			ad: wantedAssetData{
				nrReps:         5,
				loopDurationMS: 4000,
			},
			rds: map[string]wantedRepData{
				"V300": {
					nrSegs:         2,
					initURI:        "V300/init.mp4",
					mpdTimescale:   1,
					mediaTimescale: 90_000,
					duration:       360_000,
				},
				"A48": {
					nrSegs:         2,
					initURI:        "A48/init.mp4",
					mpdTimescale:   1,
					mediaTimescale: 48_000,
					duration:       192_512,
				},
			},
		},
	}
	for _, tc := range testCases {
		t.Run(tc.desc, func(t *testing.T) {
			am, tmpDir := setupAssetMgrCopy(t, tc.assetPath)
			if tc.segmentEndNr > 0 {
				// Shorten the representations of assets in temp vodFS
				err := setSegmentEndNr(path.Join(tmpDir, tc.assetPath), tc.segmentEndNr)
				require.NoError(t, err)
			}
			err := am.discoverAssets(logger)
			require.NoError(t, err)
			asset, ok := am.findAsset(tc.assetPath)
			require.True(t, ok)
			require.NotNil(t, asset)
			require.Equal(t, tc.ad.nrReps, len(asset.Reps), "nr reps in asset %s", asset.AssetPath)
			require.Equal(t, tc.ad.loopDurationMS, asset.LoopDurMS)
			for repID, wrd := range tc.rds {
				rep, ok := asset.Reps[repID]
				require.True(t, ok, "repID %s not found in asset %s", repID, asset.AssetPath)
				require.NotNil(t, rep)
				require.Equal(t, wrd.nrSegs, len(rep.Segments), "repID %s in asset %s", repID, asset.AssetPath)
				require.Equal(t, wrd.initURI, rep.InitURI)
				require.Equal(t, wrd.mpdTimescale, rep.MpdTimescale, "repID %s in asset %s", repID, asset.AssetPath)
				require.Equal(t, wrd.mediaTimescale, rep.MediaTimescale, "repID %s in asset %s", repID, asset.AssetPath)
				require.Equal(t, wrd.duration, rep.duration())
				require.Equal(t, wrd.editListOffset, rep.EditListOffset)
			}
		})
	}
}

func TestAssetRepDataRoundtrip(t *testing.T) {
	logger := slog.Default()
	am, _ := setupAssetMgrTmp(t)

	am1 := newAssetMgrBld().from(am).writeRep(true).build()
	err := am1.discoverAssets(logger)
	require.NoError(t, err)

	testCases := []struct {
		desc  string
		asset string
	}{
		{
			desc:  "CTA-Wave AAC with editlist",
			asset: "WAVE/av",
		},
		{
			desc:  "testpic_2s",
			asset: "testpic_2s",
		},
	}
	for _, tc := range testCases {
		t.Run(tc.desc, func(t *testing.T) {

			assetWrite, ok := am1.findAsset(tc.asset)
			require.True(t, ok)
			require.NotNil(t, assetWrite)

			am2 := newAssetMgrBld().from(am).missingRep(true).build()
			err = am2.discoverAssets(logger)
			require.NoError(t, err)

			assetRead, ok := am2.findAsset(tc.asset)
			require.True(t, ok)
			require.NotNil(t, assetRead)

			require.Equal(t, len(assetWrite.Reps), len(assetRead.Reps), "should have same number of representations")

			for repID, repWrite := range assetWrite.Reps {
				repRead, ok := assetRead.Reps[repID]
				require.True(t, ok, "representation should exist in cached load")

				require.Equal(t, repWrite.ID, repRead.ID)
				require.Equal(t, repWrite.ContentType, repRead.ContentType)
				require.Equal(t, repWrite.Codecs, repRead.Codecs)
				require.Equal(t, repWrite.MpdTimescale, repRead.MpdTimescale)
				require.Equal(t, repWrite.MediaTimescale, repRead.MediaTimescale)
				require.Equal(t, repWrite.InitURI, repRead.InitURI)
				require.Equal(t, repWrite.MediaURI, repRead.MediaURI)
				require.Equal(t, repWrite.DefaultSampleDuration, repRead.DefaultSampleDuration)
				require.Equal(t, repWrite.PreEncrypted, repRead.PreEncrypted)

				require.Equal(t, len(repWrite.Segments), len(repRead.Segments), "should have same number of segments")
				for i := range repWrite.Segments {
					require.Equal(t, repWrite.Segments[i].StartTime, repRead.Segments[i].StartTime)
					require.Equal(t, repWrite.Segments[i].EndTime, repRead.Segments[i].EndTime)
				}

				if len(repWrite.InitBytes) > 0 {
					require.NotNil(t, repRead.InitBytes, "initBytes should be populated from cache")
					require.Equal(t, repWrite.InitBytes, repRead.InitBytes, "initBytes should match")
					require.NotNil(t, repRead.initSeg, "initSeg should be populated from cache")
				}

				if repWrite.encData != nil && repWrite.encData.InitEnc != nil {
					require.NotNil(t, repRead.encData, "encData should be populated from cache")
					require.NotNil(t, repRead.encData.InitEnc, "encData.initEnc should be populated from cache")
					for scheme, encWrite := range repWrite.encData.InitEnc {
						encRead, ok := repRead.encData.InitEnc[scheme]
						require.True(t, ok, "scheme %s should exist in cached encData", scheme)
						require.Equal(t, encWrite.InitRaw, encRead.InitRaw, "initRaw should match for scheme %s", scheme)
					}
				}
			}
		})
	}
}

func TestWriteMissingRepData(t *testing.T) {
	logger := slog.Default()
	assetPath := "testpic_2s"
	am, tmpDir := setupAssetMgrTmp(t)

	// Step 1: Load assets with writeMissingRepData=true (no files exist yet)
	// This should write RepData files
	am1 := newAssetMgrBld().from(am).missingRep(true).build()
	err := am1.discoverAssets(logger)
	require.NoError(t, err)

	// Verify files were created
	v300Path := path.Join(tmpDir, assetPath, "V300_data.json.gz")
	a48Path := path.Join(tmpDir, assetPath, "A48_data.json.gz")
	_, err = os.Stat(v300Path)
	require.NoError(t, err, "V300_data.json.gz should have been created")
	_, err = os.Stat(a48Path)
	require.NoError(t, err, "A48_data.json.gz should have been created")

	// Step 2: Get modification time of V300 file (to verify it's not touched later)
	v300Info1, err := os.Stat(v300Path)
	require.NoError(t, err)

	// Step 3: Delete one of the RepData files
	err = os.Remove(a48Path)
	require.NoError(t, err)

	// Step 4: Load assets again with writeMissingRepData=true
	// This should only regenerate the missing A48 file, not V300
	am2 := newAssetMgrBld().from(am).missingRep(true).build()
	err = am2.discoverAssets(logger)
	require.NoError(t, err)

	// Step 5: Verify A48 file was recreated
	_, err = os.Stat(a48Path)
	require.NoError(t, err, "A48_data.json.gz should have been recreated")

	// Step 6: Verify V300 file was NOT regenerated (modification time should be the same)
	v300Info2, err := os.Stat(v300Path)
	require.NoError(t, err)
	require.Equal(t, v300Info1.ModTime(), v300Info2.ModTime(),
		"V300_data.json.gz should not have been regenerated (mod time should be unchanged)")

	// Step 7: Verify data is correct by loading the asset
	asset, ok := am2.findAsset(assetPath)
	require.True(t, ok)
	require.NotNil(t, asset)
	require.Equal(t, 5, len(asset.Reps), "should have 5 reps")

	// Verify both reps are present and have correct data
	v300, ok := asset.Reps["V300"]
	require.True(t, ok)
	require.Equal(t, 4, len(v300.Segments))
	require.Equal(t, 0, v300.Version, "RepData version should be 0")

	a48, ok := asset.Reps["A48"]
	require.True(t, ok)
	require.Equal(t, 4, len(a48.Segments))
	require.Equal(t, 0, a48.Version, "RepData version should be 0")
}

func TestAssetLookupForNameOverlap(t *testing.T) {
	am := assetMgr{}
	am.assets = make(map[string]*asset)
	am.assets["assets/testpic_2s"] = &asset{AssetPath: "assets/testpic_2s"}
	am.assets["assets/testpic_2s_1"] = &asset{AssetPath: "assets/testpic_2s_1"}
	uri := "assets/testpic_2s_1/rep1/init.mp4"
	a, ok := am.findAsset(uri)
	require.True(t, ok)
	require.Equal(t, "assets/testpic_2s_1", a.AssetPath)
}

func TestCalculateK(t *testing.T) {
	testCases := []struct {
		description     string
		segmentDuration uint64
		mediaTimescale  int
		chunkDuration   *float64
		expectedK       *uint64
		expectedError   string
	}{
		{
			description:     "nil chunk duration",
			segmentDuration: 192000,
			mediaTimescale:  96000,
			chunkDuration:   nil,
			expectedK:       nil,
		},
		{
			description:     "zero chunk duration",
			segmentDuration: 192000,
			mediaTimescale:  96000,
			chunkDuration:   Ptr(0.0),
			expectedK:       nil,
		},
		{
			description:     "negative chunk duration",
			segmentDuration: 192000,
			mediaTimescale:  96000,
			chunkDuration:   Ptr(-1.0),
			expectedK:       nil,
		},
		{
			description:     "zero media timescale",
			segmentDuration: 192000,
			mediaTimescale:  0,
			chunkDuration:   Ptr(1.0),
			expectedK:       nil,
		},
		{
			description:     "k=4",
			segmentDuration: 192000,
			mediaTimescale:  96000,
			chunkDuration:   Ptr(0.5),
			expectedK:       Ptr(uint64(4)),
		},
		{
			description:     "k=1, returns nil",
			segmentDuration: 192000,
			mediaTimescale:  96000,
			chunkDuration:   Ptr(2.0),
			expectedK:       nil,
		},
		{
			description:     "rounding up",
			segmentDuration: 192000,
			mediaTimescale:  96000,
			chunkDuration:   Ptr(0.57), // 3.5087... -> 4
			expectedK:       Ptr(uint64(4)),
		},
		{
			description:     "rounding down",
			segmentDuration: 192000,
			mediaTimescale:  96000,
			chunkDuration:   Ptr(0.58), // 3.448... -> 3
			expectedK:       Ptr(uint64(3)),
		},
		{
			description:     "chunk duration greater than segment duration",
			segmentDuration: 192000,
			mediaTimescale:  96000,
			chunkDuration:   Ptr(2.5), // 2.5s > 2.0s segment duration
			expectedK:       nil,
			expectedError:   "chunk duration 2.50s must be less than or equal to segment duration 2.00s",
		},
	}

	for _, tc := range testCases {
		t.Run(tc.description, func(t *testing.T) {
			gotK, err := calculateK(tc.segmentDuration, tc.mediaTimescale, tc.chunkDuration)

			if tc.expectedError != "" {
				require.Error(t, err)
				require.Equal(t, tc.expectedError, err.Error())
				require.Nil(t, gotK)
				return
			}

			require.NoError(t, err)
			if tc.expectedK == nil {
				require.Nil(t, gotK)
			} else {
				require.NotNil(t, gotK)
				require.Equal(t, *tc.expectedK, *gotK)
			}
		})
	}
}

func copyDir(srcDir, dstDir string) error {
	if err := os.MkdirAll(dstDir, 0755); err != nil {
		return err
	}
	return filepath.Walk(srcDir, func(walkPath string, info os.FileInfo, err error) error {
		relPath, err := filepath.Rel(srcDir, walkPath)
		if err != nil {
			return err
		}
		if relPath == "." {
			return nil
		}
		dstPath := filepath.Join(dstDir, relPath)
		if info.IsDir() {
			return os.MkdirAll(dstPath, 0755)
		}
		data, err := os.ReadFile(walkPath)
		if err != nil {
			return err
		}
		return os.WriteFile(dstPath, data, 0644)
	})
}

// Set the endNumber attribute in all MPDs SegmentTemplate elements
func setSegmentEndNr(assetDir string, endNumber uint32) error {
	files, err := os.ReadDir(assetDir)
	if err != nil {
		return err
	}
	for _, file := range files {
		if filepath.Ext(file.Name()) == ".mpd" {
			mpdPath := filepath.Join(assetDir, file.Name())

			mpd, err := m.ReadFromFile(mpdPath)
			if err != nil {
				return err
			}
			p := mpd.Periods[0]
			for _, a := range p.AdaptationSets {
				stl := a.SegmentTemplate
				stl.EndNumber = &endNumber
			}
			mpdRaw, err := mpd.WriteToString("", false)
			if err != nil {
				return err
			}
			err = os.WriteFile(mpdPath, []byte(mpdRaw), 0644)
			if err != nil {
				return err
			}
		}
	}
	return nil
}

func setupAssetMgr() *assetMgr {
	vodFS := os.DirFS("testdata/assets")
	return newAssetMgrBld().vodFs(vodFS).build()
}

func setupAssetMgrTmp(t *testing.T) (*assetMgr, string) {
	tmpDir := t.TempDir()
	vodFS := os.DirFS("testdata/assets")
	repFS := rwfs.OSDirFS(tmpDir)
	return newAssetMgrBld().vodFs(vodFS).repFs(repFS).build(), tmpDir
}

func setupAssetMgrCopy(t *testing.T, assetPath string) (*assetMgr, string) {
	tmpDir := t.TempDir()

	vodRoot := "testdata/assets"
	src := path.Join(vodRoot, assetPath)
	dst := path.Join(tmpDir, assetPath)

	err := copyDir(src, dst)
	require.NoError(t, err)

	vodFS := os.DirFS(tmpDir)
	return newAssetMgrBld().vodFs(vodFS).build(), tmpDir
}
