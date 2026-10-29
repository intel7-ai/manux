function closeTransfers(transfer) {
  for (const resource of transfer) {
    try { resource?.close?.(); } catch { /* Already detached resources need no cleanup. */ }
  }
}

/** One frame in flight, including bitmap creation. A failed worker needs a new client. */
export class VisionClient {
  constructor({
    workerFactory = () => new Worker('/src/vision-worker.js'),
    createBitmap = (video) => createImageBitmap(video),
    initTimeoutMs = 45000,
    frameTimeoutMs = 10000,
  } = {}) {
    this.workerFactory = workerFactory;
    this.createBitmap = createBitmap;
    this.initTimeoutMs = initTimeoutMs;
    this.frameTimeoutMs = frameTimeoutMs;
    this.worker = null;
    this.pending = new Map();
    this.nextId = 1;
    this.busy = false;
    this.closed = false;
    this.ready = false;
    this.initialization = null;
  }

  request(type, payload = {}, transfer = []) {
    if (this.closed || !this.worker) {
      closeTransfers(transfer);
      return Promise.reject(new Error(this.closed ? '인식 처리가 종료되었습니다.' : '인식 모델이 아직 준비되지 않았습니다.'));
    }
    return new Promise((resolve, reject) => {
      const id = this.nextId++;
      const timer = setTimeout(() => {
        // Timing out only the promise would allow the next frame to queue behind
        // a stuck inference. End the entire session, including all pending work.
        this.close(new Error(type === 'init' ? '모델 준비 시간이 초과되었습니다.' : '영상 처리 시간이 초과되었습니다.'));
      }, type === 'init' ? this.initTimeoutMs : this.frameTimeoutMs);
      this.pending.set(id, { resolve, reject, timer });
      try {
        this.worker.postMessage({ ...payload, id, type }, transfer);
      } catch (error) {
        clearTimeout(timer);
        this.pending.delete(id);
        closeTransfers(transfer);
        reject(error);
      }
    });
  }

  init() {
    if (this.closed) return Promise.reject(new Error('인식 처리가 종료되었습니다.'));
    // Repeated Start clicks share one worker and one model initialization.
    if (this.initialization) return this.initialization;
    try {
      const worker = this.workerFactory();
      this.worker = worker;
      worker.onmessage = ({ data }) => {
        if (this.closed || this.worker !== worker || !data) return;
        const pending = this.pending.get(data.id);
        if (!pending) return;
        clearTimeout(pending.timer);
        this.pending.delete(data.id);
        if (data.ok) pending.resolve(data);
        else pending.reject(new Error(data.error || '인식 처리 오류'));
      };
      worker.onerror = (event) => this.close(new Error(event.message || '인식 처리 오류'));
      worker.onmessageerror = () => this.close(new Error('인식 결과를 읽을 수 없습니다.'));
      this.initialization = this.request('init').then((result) => {
        if (this.closed) throw new Error('인식 처리가 종료되었습니다.');
        this.ready = true;
        return result;
      }, (error) => {
        this.close(error);
        throw error;
      });
      return this.initialization;
    } catch (error) {
      this.close(error);
      return Promise.reject(error);
    }
  }

  async recognize(video, timestamp) {
    if (this.busy || this.closed) return null;
    if (!this.ready) throw new Error('인식 모델이 아직 준비되지 않았습니다.');
    this.busy = true;
    try {
      const bitmap = await this.createBitmap(video);
      if (this.closed) {
        closeTransfers([bitmap]);
        return null;
      }
      // Successful transfer gives ownership to vision-worker's finally block.
      // request() closes the bitmap itself if the transfer cannot be completed.
      return await this.request('frame', { bitmap, timestamp }, [bitmap]);
    } finally {
      this.busy = false;
    }
  }

  close(error = new Error('인식 처리가 종료되었습니다.')) {
    if (this.closed) return;
    this.closed = true;
    this.ready = false;
    this.busy = false;
    if (this.worker) {
      this.worker.onmessage = null;
      this.worker.onerror = null;
      this.worker.onmessageerror = null;
      this.worker.terminate();
      this.worker = null;
    }
    for (const pending of this.pending.values()) {
      clearTimeout(pending.timer);
      pending.reject(error);
    }
    this.pending.clear();
  }
}
