import * as alphaTab from "@coderline/alphatab";

export function mergeScores(
  scores: alphaTab.model.Score[],
  settings: alphaTab.Settings,
): alphaTab.model.Score {
  if (scores.length === 0) {
    throw new Error("没有可合并的乐谱");
  }

  const target = scores[0];
  if (!target) {
    throw new Error("没有可合并的乐谱");
  }

  for (const addition of scores.slice(1)) {
    if (addition.masterBars.length === 0) {
      continue;
    }
    if (addition.tracks.length !== target.tracks.length) {
      throw new Error("多页曲谱的音轨数量不一致");
    }

    for (let trackIndex = 0; trackIndex < target.tracks.length; trackIndex += 1) {
      const targetTrack = target.tracks[trackIndex];
      const additionTrack = addition.tracks[trackIndex];
      if (!targetTrack || !additionTrack || additionTrack.staves.length !== targetTrack.staves.length) {
        throw new Error("多页曲谱的谱表结构不一致");
      }
      for (let staffIndex = 0; staffIndex < targetTrack.staves.length; staffIndex += 1) {
        const bars = additionTrack.staves[staffIndex]?.bars;
        if (!bars || bars.length !== addition.masterBars.length) {
          throw new Error("多页曲谱的小节数量不一致");
        }
      }
    }

    for (const masterBar of addition.masterBars) {
      target.addMasterBar(masterBar);
    }
    for (let trackIndex = 0; trackIndex < target.tracks.length; trackIndex += 1) {
      const targetTrack = target.tracks[trackIndex]!;
      const additionTrack = addition.tracks[trackIndex]!;
      for (let staffIndex = 0; staffIndex < targetTrack.staves.length; staffIndex += 1) {
        const targetStaff = targetTrack.staves[staffIndex]!;
        for (const bar of additionTrack.staves[staffIndex]!.bars) {
          targetStaff.addBar(bar);
        }
      }
    }
  }

  target.finish(settings);
  return target;
}
