.libPaths(c("/tmp/Rlibs", .libPaths()))

cat("Current libPaths 1:\n")
print(.libPaths())
if (dir.exists("/tmp/Rlibs")) {
    print(list.files("/tmp/Rlibs"))
}

# Create /tmp/Rlibs if it doesn't exist
if (!dir.exists("/tmp/Rlibs")) {
    dir.create("/tmp/Rlibs", recursive = TRUE)
}

cat("\nInstalled packages in /tmp/Rlibs:\n")
print(list.files("/tmp/Rlibs"))

# Install packages if they're not available
if (!require("httr", quietly = TRUE)) {
    cat("Installing httr...\n")
    install.packages("httr", lib = "/tmp/Rlibs", repos = "https://cloud.r-project.org", dependencies = FALSE)
}

if (!require("jsonlite", quietly = TRUE)) {
    cat("Installing jsonlite...\n")
    install.packages("jsonlite", lib = "/tmp/Rlibs", repos = "https://cloud.r-project.org", dependencies = FALSE)
}

cat("Current libPaths 2:\n")
print(.libPaths())
if (dir.exists("/tmp/Rlibs")) {
    print(list.files("/tmp/Rlibs"))
}

# Load the packages
library("httr")
library("jsonlite")

source("r_client_stubs.R")
source("r_func_helper.R")

# Entry for R function process
args <- commandArgs(trailingOnly = TRUE)
func_name <- args[1]
user_args <- fromJSON(args[2])
invocation_id <- args[3]

faasr_source_r_files(file.path("/tmp/functions", invocation_id))

# Execute User function
result <- faasr_run_user_function(func_name, user_args)
