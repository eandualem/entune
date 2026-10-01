// Entune.app's executable: run the installed Entune as a child process and stay its parent.
//
// macOS charges Microphone, Input Monitoring and Accessibility to a process's
// "responsible" app. A child started here keeps this bundle as that app, so the prompts
// and System Settings show Entune, not Python or the terminal Entune was installed from.
//
// Nothing that changes between Entune versions lives in the bundle. The command to run
// comes from this bundle's preferences (`LaunchCommand`, written by `entune`), so the
// bundle's ad hoc signature, and every permission granted to it, survives upgrades.
// Rebuild with packaging/app-launcher/build.sh only when this file changes: a new binary
// is a new app identity to macOS and costs every user their permissions once.

#import <AppKit/AppKit.h>
#include <signal.h>
#include <spawn.h>
#include <sys/wait.h>

extern char **environ;

static NSString *const Reinstall = @"Open Terminal and run:\n\nuv tool install entune\nentune";
static const NSTimeInterval QuitGrace = 10;

@interface Launcher : NSObject <NSApplicationDelegate>
@end

@implementation Launcher {
    pid_t _child;
    dispatch_source_t _exit;
    BOOL _quitting;
}

- (void)fail:(NSString *)message {
    NSAlert *alert = [NSAlert new];
    alert.messageText = @"Entune can't start";
    alert.informativeText = message;
    [NSApp activateIgnoringOtherApps:YES];
    [alert runModal];
    [NSApp terminate:nil];
}

- (void)applicationDidFinishLaunching:(NSNotification *)note {
    NSArray *command = [[NSUserDefaults standardUserDefaults] arrayForKey:@"LaunchCommand"];
    if (command.count == 0) return [self fail:Reinstall];
    for (id part in command)
        if (![part isKindOfClass:NSString.class]) return [self fail:Reinstall];
    if (![[NSFileManager defaultManager] isExecutableFileAtPath:command[0]])
        return [self fail:[NSString stringWithFormat:
            @"The Entune installation this app runs is gone:\n%@\n\n%@", command[0], Reinstall]];

    setenv("ENTUNE_APP", NSBundle.mainBundle.bundlePath.fileSystemRepresentation, 1);
    char **argv = calloc(command.count + 1, sizeof(char *));
    for (NSUInteger i = 0; i < command.count; i++) argv[i] = strdup([command[i] UTF8String]);
    int err = posix_spawn(&_child, argv[0], NULL, NULL, argv, environ);
    if (err != 0)
        return [self fail:[NSString stringWithFormat:@"Could not run %@: %s", command[0], strerror(err)]];

    _exit = dispatch_source_create(DISPATCH_SOURCE_TYPE_PROC, (uintptr_t)_child,
                                   DISPATCH_PROC_EXIT, dispatch_get_main_queue());
    dispatch_source_set_event_handler(_exit, ^{
        int status;
        waitpid(self->_child, &status, 0);
        self->_child = 0;
        dispatch_source_cancel(self->_exit);
        if (self->_quitting) [NSApp replyToApplicationShouldTerminate:YES];
        else [NSApp terminate:nil];
    });
    dispatch_resume(_exit);

    // `kill` and similar: treat like Quit.
    signal(SIGTERM, SIG_IGN);
    dispatch_source_t term = dispatch_source_create(DISPATCH_SOURCE_TYPE_SIGNAL, SIGTERM, 0,
                                                    dispatch_get_main_queue());
    dispatch_source_set_event_handler(term, ^{ [NSApp terminate:nil]; });
    dispatch_resume(term);
}

// Opening Entune again while it runs: bring its window forward.
- (BOOL)applicationShouldHandleReopen:(NSApplication *)app hasVisibleWindows:(BOOL)visible {
    NSInteger port = [[NSUserDefaults standardUserDefaults] integerForKey:@"Port"] ?: 4187;
    NSURL *url = [NSURL URLWithString:[NSString stringWithFormat:@"http://127.0.0.1:%ld/api/window", (long)port]];
    NSMutableURLRequest *request = [NSMutableURLRequest requestWithURL:url];
    request.HTTPMethod = @"POST";
    [[NSURLSession.sharedSession dataTaskWithRequest:request] resume];
    return NO;
}

// Quit Entune itself first, so it can finish saving audio, then follow it out.
- (NSApplicationTerminateReply)applicationShouldTerminate:(NSApplication *)app {
    if (_child <= 0) return NSTerminateNow;
    _quitting = YES;
    pid_t child = _child;
    if (![[NSRunningApplication runningApplicationWithProcessIdentifier:child] terminate])
        kill(child, SIGTERM);
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(QuitGrace * NSEC_PER_SEC)),
                   dispatch_get_main_queue(), ^{
        if (self->_child == child) kill(child, SIGTERM);
    });
    return NSTerminateLater;
}
@end

int main(void) {
    @autoreleasepool {
        NSApplication *app = [NSApplication sharedApplication];
        Launcher *delegate = [Launcher new];
        app.delegate = delegate;
        [app run];
    }
    return 0;
}
