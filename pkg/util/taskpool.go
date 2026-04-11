package util

import (
	"context"
	"sync"
)

type TaskPoolOptions int

const (
	StopOnError TaskPoolOptions = 1 << iota
)

type Task[T any] func() (T, error)

type Result[T any] struct {
	Ok   T
	Fail error
}

type TaskPool[T any] struct {
	poolSize int
	options  TaskPoolOptions
	ctx      context.Context
	cancel   context.CancelFunc

	started   bool
	inClosed  bool
	outClosed bool
	results   []Result[T]
	stateLock sync.Mutex

	input  chan Task[T]
	output chan Result[T]

	workers   sync.WaitGroup
	tasks     sync.WaitGroup
	collector sync.WaitGroup
}

// NewTaskPool creates a new TaskPool instance
func NewTaskPool[T any](poolSize int, bufferSize int, options TaskPoolOptions) *TaskPool[T] {
	input := make(chan Task[T], bufferSize*poolSize)
	output := make(chan Result[T], bufferSize*poolSize)

	ctx, cancel := context.WithCancel(context.Background())

	return &TaskPool[T]{
		poolSize: poolSize,
		options:  options,
		input:    input,
		output:   output,
		ctx:      ctx,
		cancel:   cancel,
	}
}

// NewTaskPool creates a new TaskPool instance
func NewTaskPoolCtx[T any](ctx context.Context, poolSize int, bufferSize int, options TaskPoolOptions) *TaskPool[T] {
	input := make(chan Task[T], bufferSize*poolSize)
	output := make(chan Result[T], bufferSize*poolSize)

	ctx, cancel := context.WithCancel(ctx)

	return &TaskPool[T]{
		poolSize: poolSize,
		options:  options,
		input:    input,
		output:   output,
		ctx:      ctx,
		cancel:   cancel,
	}
}

// Start begins processing tasks with the specified number of workers
func (tp *TaskPool[T]) Start() {
	if !tp.setStarted() {
		return
	}

	if tp.poolSize == 0 {
		// Sequential processing - handled differently
		return
	}

	// Start collector to drain output
	go tp.collect()

	// Start worker goroutines
	for i := 0; i < tp.poolSize; i++ {
		go tp.worker()
	}
}

// AddTask adds a single task to the Task pool
func (tp *TaskPool[T]) AddTask(item Task[T]) {
	if tp.poolSize == 0 {
		// Sequential processing
		tp.processSequentially(item)
		return
	}

	// Input was closed because ctx cancelled
	if tp.isInputClosed() {
		return
	}

	// Count the task immediately
	tp.tasks.Add(1)

	// Enqueue in background so a worker that calls AddTask won't block on the send.
	go func() {
		select {
		case tp.input <- item:
			// enqueued successfully
			return
		case <-tp.ctx.Done():
			// pool canceled before enqueue -> undo earlier Add
			tp.tasks.Done()
			return
		}
	}()
}

// AddTasks adds multiple tasks to the Task pool
func (tp *TaskPool[T]) AddTasks(items []Task[T]) {
	for _, item := range items {
		tp.AddTask(item)
	}
}

// Join waits for all tasks to complete and returns results and errors
func (tp *TaskPool[T]) Join() []Result[T] {
	if tp.poolSize == 0 {
		// Sequentially processed
		return tp.results
	}

	// Wait for all tasks to complete
	tp.tasks.Wait()

	// Close input channel
	tp.setInputClosed()

	// Cancel the context to finish the workers
	tp.cancel()

	// Wait for workers to finish
	tp.workers.Wait()

	// Close the output channel
	tp.setOutputClosed()

	// Collect the output channel
	tp.collector.Wait()
	return tp.results
}

// Close immediately terminates the Task pool without waiting for tasks to complete
func (tp *TaskPool[T]) Close() {
	tp.cancel()
	tp.setInputClosed()
	tp.setOutputClosed()
}

// worker processes tasks from the input channel
func (tp *TaskPool[T]) worker() {
	tp.workers.Add(1)
	defer tp.workers.Done()

	for {
		select {
		case item, ok := <-tp.input:
			if !ok {
				// Input closed -> normal exit
				return
			}
			tp.processItem(item)

		case <-tp.ctx.Done():
			// Non-blocking drain of any items currently in the buffer
			for {
				select {
				case _, ok := <-tp.input:
					if !ok {
						// input closed -> nothing left
						return
					}
					// this was a queued task we need to account for
					tp.tasks.Done()
				default:
					// no more queued tasks immediately available
					return
				}
			}
		}
	}
}

// processItem processes a single item and handles the result
func (tp *TaskPool[T]) processItem(item Task[T]) {
	defer tp.tasks.Done()

	out, err := item()
	tp.output <- Result[T]{out, err}

	if tp.options&StopOnError != 0 {
		tp.cancel()
		tp.setInputClosed()
	}
}

// processSequentially processes items one by one (when poolSize == 0)
func (tp *TaskPool[T]) processSequentially(item Task[T]) {
	if !tp.isStarted() || tp.isInputClosed() {
		return
	}

	out, err := item()
	result := Result[T]{out, err}

	if err != nil {
		tp.setInputClosed()
	}

	tp.stateLock.Lock()
	defer tp.stateLock.Unlock()
	tp.results = append(tp.results, result)
}

// collector collects results from the output channel
func (tp *TaskPool[T]) collect() {
	tp.collector.Add(1)
	defer tp.collector.Done()

	// Collect results
	results := []Result[T]{}

	for res := range tp.output {
		results = append(results, res)
	}

	tp.stateLock.Lock()
	defer tp.stateLock.Unlock()
	tp.results = results
}

// isStarted thread-safe checks if its started
func (tp *TaskPool[T]) isStarted() bool {
	tp.stateLock.Lock()
	defer tp.stateLock.Unlock()
	return tp.started
}

// setStarted thread-safe sets started
func (tp *TaskPool[T]) setStarted() bool {
	tp.stateLock.Lock()
	defer tp.stateLock.Unlock()
	if tp.started {
		return false
	}
	tp.started = true
	return true
}

// isInputClosed thread-safe checks if its closed
func (tp *TaskPool[T]) isInputClosed() bool {
	tp.stateLock.Lock()
	defer tp.stateLock.Unlock()
	return tp.inClosed
}

// setInputClosed thread-safe sets closed and close input channel
func (tp *TaskPool[T]) setInputClosed() bool {
	tp.stateLock.Lock()
	defer tp.stateLock.Unlock()
	if tp.inClosed {
		return false
	}
	tp.inClosed = true
	close(tp.input)
	return true
}

// setOutputClosed thread-safe sets closed and close input channel
func (tp *TaskPool[T]) setOutputClosed() bool {
	tp.stateLock.Lock()
	defer tp.stateLock.Unlock()
	if tp.outClosed {
		return false
	}
	tp.outClosed = true
	close(tp.output)
	return true
}
